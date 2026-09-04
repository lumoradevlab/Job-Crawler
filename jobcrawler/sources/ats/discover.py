"""Growing the ATS board lists by probing company names against every ATS."""

import concurrent.futures as futures
import re
from datetime import datetime, timedelta
from html import unescape

from ...parse.text import COMPANY_NOISE
from .ashby import ASHBY_BOARDS, ASHBY_LIST
from .greenhouse import GH_LIST, GREENHOUSE_BOARDS
from .lever import LEVER_BOARDS, LEVER_LIST
from .smartrecruiters import SMARTRECRUITERS_BOARDS
from .workable import WORKABLE_BOARDS, WORKABLE_LIST


# The ATS sources are the highest-signal ones here and the hardest to grow:
# there is no company index anywhere, so a slug is only reachable if you
# already know the company uses that ATS, and every list above was built by
# hand-probing candidates and keeping whatever answered.
#
# But every aggregator result names a company. So the low-signal sources can
# be made to feed the high-signal ones: take the companies LinkedIn, Built In
# and Adzuna turned up, normalise each name into the slugs an ATS might host
# it under, and keep the ones that answer. That is --discover, and it means
# the crawler grows its own best sources a little on every run.

SR_PROBE = "https://api.smartrecruiters.com/v1/companies/{}/postings?limit=1"

# The only proof a slug is real is that it answers with at least one live job.
# Greenhouse, Ashby, Lever and Workable all 404 an unknown slug; SmartRecruiters
# answers 200 with an empty list. But a real company that simply isn't hiring
# looks exactly like a typo on every one of them, so "has jobs" is the rule
# either way — and a miss is re-probed after DISCOVER_RETRY_DAYS, which is what
# turns a company that was between postings back into a board.
BOARD_PROBES = {
    "greenhouse": (GH_LIST, lambda d: (d or {}).get("jobs")),
    "lever": (LEVER_LIST, lambda d: d if isinstance(d, list) else None),
    "ashby": (ASHBY_LIST, lambda d: (d or {}).get("jobs")),
    "workable": (WORKABLE_LIST, lambda d: (d or {}).get("jobs")),
    "smartrecruiters": (SR_PROBE, lambda d: (d or {}).get("content")),
}
BUILTIN_BOARDS = {
    "greenhouse": GREENHOUSE_BOARDS,
    "lever": LEVER_BOARDS,
    "ashby": ASHBY_BOARDS,
    "workable": WORKABLE_BOARDS,
    "smartrecruiters": SMARTRECRUITERS_BOARDS,
}

# Probed in this order and stopped at the first hit: a company uses one ATS,
# so recognising it on Greenhouse saves the other four requests.
PROBE_ORDER = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]

DISCOVER_CAP = 150          # candidates per run; the rest wait for the next
DISCOVER_RETRY_DAYS = 30    # how long a miss is remembered before re-probing


def slug_candidates(company):
    """A company name -> the slugs an ATS might plausibly host it under.

    "Epic Games, Inc." is epicgames on one board and epic-games on another,
    and nothing anywhere says which, so both are tried. Aggregator company
    fields are not always company names — HN's is the first line of a post —
    so anything carrying a URL or a pipe is left alone rather than mangled.
    """
    name = unescape(company or "").strip()
    if not name or len(name) > 40 or re.search(r"https?:|[|/@]", name):
        return []
    # Dropped rather than treated as separators: O'Reilly is oreilly, and
    # Alarm.com is alarmcom on its board, not "alarm" plus "com".
    name = re.sub(r"[\u2018\u2019'`.]", "", name)
    words = re.sub(r"[^a-z0-9]+", " ", COMPANY_NOISE.sub(" ", name).lower()).split()
    if not words:
        return []
    out = ["".join(words)]
    if len(words) > 1:
        out.append("-".join(words))
        # "Epic Games" is plausibly hosted as "epic". "Bank of America" is not
        # plausibly "bank", so only a two-word name gives up its head word.
        if len(words) == 2 and len(words[0]) >= 4:
            out.append(words[0])
    return [s for s in dict.fromkeys(out) if 2 <= len(s) <= 40]


def probe_board(slug, ctx):
    """Which ATS hosts this slug with live jobs, or None if none of them do."""
    for ats in PROBE_ORDER:
        url, jobs_of = BOARD_PROBES[ats]
        if jobs_of(ctx.fetch.get_json(url.format(slug), tries=1, timeout=12)):
            return ats
    return None


def known_slugs(boards):
    """Every slug already in a list, built-in or discovered.

    Seeded from the built-ins as well as the found ones: without that, a
    third of a first run is spent re-proving that Lyft is on Greenhouse.
    """
    known = {s for slugs in BUILTIN_BOARDS.values() for s in slugs}
    return known | {s for slugs in (boards.get("found") or {}).values()
                    for s in slugs}


def discover_boards(companies, boards, today, ctx):
    """Probe company names against every ATS and remember what answered."""
    found = boards.setdefault("found", {})
    missed = boards.setdefault("missed", {})
    known = known_slugs(boards)
    stale = (datetime.strptime(today, "%Y-%m-%d")
             - timedelta(days=DISCOVER_RETRY_DAYS)).strftime("%Y-%m-%d")

    queue = {}
    for company in companies:
        for slug in slug_candidates(company):
            if slug in known or slug in queue:
                continue
            if missed.get(slug, "") >= stale:   # probed lately, still a miss
                continue
            queue[slug] = company

    slugs = list(queue)[:DISCOVER_CAP]
    if not slugs:
        print("[discover] no new company names to probe")
        return 0
    waiting = len(queue) - len(slugs)
    print(f"[discover] probing {len(slugs)} candidate slugs across "
          f"{len(PROBE_ORDER)} ATSes"
          + (f", {waiting} more next run" if waiting else ""))

    hits = 0
    with futures.ThreadPoolExecutor(max_workers=4) as ex:
        probe = lambda slug: probe_board(slug, ctx)
        for slug, ats in zip(slugs, ex.map(probe, slugs)):
            if ats:
                found.setdefault(ats, []).append(slug)
                missed.pop(slug, None)
                hits += 1
                print(f"  + {ats}: {slug}   ({queue[slug]})")
            else:
                missed[slug] = today
    print(f"  {hits} new board{'' if hits == 1 else 's'}, "
          f"{len(slugs) - hits} slugs did not answer")
    return hits
