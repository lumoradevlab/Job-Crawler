"""One crawl loop for all five company-ATS boards.

Greenhouse, Ashby, Lever, Workable and SmartRecruiters were five functions
doing the same six things: resolve a slug list, fan out over it in a six-worker
pool, walk the returned postings, apply the title gate, map each hit into the
shared record, and — for the two whose listings carry no body — fetch each hit
a second time. Only the middle step genuinely differed. The rest was copied,
which is why the concurrency, the gate and the seen-job skip had drifted into
three slightly different shapes across the five.

What differs per board is now a BoardSpec: where to ask, how to find the
postings in the reply, and how to read one into a record. Everything else
happens here, once.

The two shapes are one shape. A spec with no detail_url stops after listing;
a spec with one pays for a second request per hit, but only for hits that are
new — the listing is one request per company and cheap, while a detail fetch
is one request per posting and is the expensive part worth not repeating.
"""

import concurrent.futures as futures
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from ...filters.rules import relevant
from ...store.seen import job_key
from .boards import board_list

WORKERS = 6


@dataclass(frozen=True)
class BoardSpec:
    """Everything that differs between one ATS and the next."""

    name: str
    boards: Tuple[str, ...]
    list_url: str

    # (payload) -> the raw postings in it
    jobs_of: Callable
    # (raw) -> the posting's title, for the gate
    title_of: Callable
    # (raw, token, payload) -> the shared record
    to_posting: Callable

    # (raw) -> True to ignore this posting outright, before it is even counted
    skip: Optional[Callable] = None

    # Listings that carry no description: fetch each hit once more.
    detail_url: Optional[Callable] = None      # (record) -> url
    merge_detail: Optional[Callable] = None    # (record, payload) -> None
    # What --discover asks to find out whether a slug is real. Defaults to
    # the listing URL; SmartRecruiters overrides it because its listing takes
    # an offset and a one-result probe is cheaper.
    probe_url: Optional[str] = None

    # SmartRecruiters pages its listing; the rest answer in one request.
    paged: bool = False
    page_size: int = 100
    max_offset: int = 400

    tries: int = 2


def _payloads(spec, token, ctx):
    """Every listing page for one company. Usually exactly one."""
    if not spec.paged:
        yield ctx.fetch.get_json(spec.list_url.format(token), tries=spec.tries)
        return
    offset = 0
    while offset < spec.max_offset:
        data = ctx.fetch.get_json(spec.list_url.format(token, offset),
                                  tries=spec.tries)
        yield data
        batch = spec.jobs_of(data)
        if len(batch) < spec.page_size:
            return
        offset += spec.page_size


def crawl_boards(spec, cfg, ctx):
    """List every board for one ATS, gate the titles, detail the hits."""
    boards = cfg.override_for(spec.name) or board_list(spec.name, spec.boards, ctx)
    ctx.report.source(spec.name, f"listing {len(boards)} company boards")

    def one_board(token):
        # Counting inside the worker and summing after keeps this free of
        # shared mutable state, so the pool needs no lock.
        scanned, found = 0, []
        for data in _payloads(spec, token, ctx):
            for raw in spec.jobs_of(data):
                if spec.skip and spec.skip(raw):
                    continue
                scanned += 1
                title = (spec.title_of(raw) or "").strip()
                if not relevant(title, cfg.filters, spec.name, ctx.report):
                    continue
                found.append(spec.to_posting(raw, token, data))
        return scanned, found

    scanned, listed = 0, []
    with futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for n, rows in ex.map(one_board, boards):
            scanned += n
            listed.extend(rows)
    ctx.report.detail(f"{scanned} postings scanned, {len(listed)} Android/mobile titles")

    hits = listed
    if spec.detail_url:
        # The listing was one request per company; each body is one request
        # per posting, so never re-read one an earlier run already stored.
        hits = [j for j in listed if job_key(j) not in ctx.seen_keys]
        if ctx.seen_keys:
            ctx.report.detail(f"{len(hits)} of those are new; skipping the rest")

        def detail(j):
            data = ctx.fetch.get_json(spec.detail_url(j), tries=spec.tries)
            if data:
                spec.merge_detail(j, data)
            return j

        with futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            hits = list(ex.map(detail, hits))

    ctx.report.detail(f"{sum(1 for j in hits if j.remote)} of them are remote")
    return hits


def specs():
    """Every ATS spec, in the order --discover probes them.

    A company uses one ATS, so recognising it on Greenhouse saves the other
    four requests — and Greenhouse is much the commonest, so it goes first.
    """
    from .ashby import ASHBY
    from .greenhouse import GREENHOUSE
    from .lever import LEVER
    from .smartrecruiters import SMARTRECRUITERS
    from .workable import WORKABLE
    return (GREENHOUSE, LEVER, ASHBY, WORKABLE, SMARTRECRUITERS)


def make_source(spec):
    """Turn a spec into the crawl_x(cfg, ctx) the registry expects."""
    def crawl(cfg, ctx):
        return crawl_boards(spec, cfg, ctx)
    crawl.__name__ = "crawl_" + spec.name
    crawl.__doc__ = f"Crawl every {spec.name} company board. See BoardSpec."
    crawl.spec = spec
    return crawl
