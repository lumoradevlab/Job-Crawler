"""One crawl per country, then a filtered batch to each subscriber.

The measurement that shapes this: a full crawl of one country takes about
four minutes, and the boards do not know who is asking. So crawling is
shared and only the filtering and sending are per person — which means the
cost of a run grows with countries, not with readers. A hundred subscribers
cost the same crawl as one.

The per-person half is deliberately cheap: a pass over postings already in
memory, then one message batch. Nothing here re-fetches anything.
"""

from ..config import CrawlConfig, FilterConfig
from ..context import RunContext
from ..filters.countries import resolve as resolve_country
from ..net.http import Fetcher
from ..net.ratelimit import HostPolicy, RateLimiter
from ..pipeline.collect import collect
from ..pipeline.select import select
from ..roles import combine
from ..sources.registry import SOURCES
from ..store.seen import job_key
from .telegram import Blocked


def crawl_country(code, sources, days, pages, report, delay=4.0):
    """Every posting one country's crawl turned up, ungated by role.

    The role gate is left off here on purpose: subscribers want different
    disciplines from the same crawl, so gating now would mean one crawl per
    role rather than one per country — seventeen times the work for the same
    postings.
    """
    country = resolve_country(code)
    # The widest profile, so nothing a subscriber might want is dropped
    # before the per-person pass gets to see it.
    widest = combine(["all"])
    filters = FilterConfig(subject=widest.pattern(),
                           role=widest.role_pattern(),
                           country=country, days=days)
    cfg = CrawlConfig(keywords=widest.queries, sources=tuple(sources),
                      location=country.linkedin, pages=pages, days=days,
                      delay=delay, country=country, filters=filters)
    limiter = RateLimiter()
    limiter.set_policy("www.linkedin.com", HostPolicy(gap=delay, jitter=1.5))
    ctx = RunContext(fetch=Fetcher(limiter=limiter, report=report),
                     report=report)
    outcome = collect(cfg, ctx, SOURCES)
    jobs, _ = select(outcome.postings, filters)
    report.line(f"[{code}] {len(jobs)} postings after the shared gate")
    return jobs, cfg


def for_subscriber(sub, jobs, today):
    """The postings this subscriber has not been sent, in their disciplines.

    Their own seen list decides what is new, not a shared one: two readers
    want different roles from the same crawl, and "already sent" is true of
    one and not the other.
    """
    profile = combine(sub.roles)
    pattern, roles = profile.pattern(), profile.role_pattern()
    out = []
    for j in jobs:
        title = j.title or ""
        if not (pattern.search(title) and roles.search(title)):
            continue
        if not sub.wants(j.company):
            continue
        if sub.has_seen(j.url or job_key(j)):
            continue
        out.append(j)
    return out


def deliver(bot, subs, sub, jobs, country, today, limit, report):
    """Send one subscriber their batch. Returns how many arrived.

    A 403 means the reader has blocked the bot, which is an unsubscribe
    wearing an error's clothes — so they are removed rather than retried
    forever. Every other failure is left alone: their state is not written,
    and the next run tries again.
    """
    if not jobs:
        return 0

    from .telegram import format_digest
    fresh, overflow = jobs[:limit], jobs[limit:]
    try:
        ids = bot.send_postings(fresh, key_of=lambda j: j.url or job_key(j),
                                country=country, chat_id=sub.chat_id)
        if overflow:
            bot.send_to(sub.chat_id, format_digest(overflow, len(fresh)))
    except Blocked:
        subs.remove(sub.chat_id)
        report.line(f"  {sub.chat_id} blocked the bot — unsubscribed")
        return 0

    # Recorded only after Telegram accepted them: a send that failed must
    # come back tomorrow rather than being silently dropped.
    #
    # The individually-sent ones get a full record, because a tap on one has
    # to find its title, its company and the message to rewrite. The summary
    # entries carry no buttons and so need only the dated seen key.
    for j in fresh:
        key = j.url or job_key(j)
        sub.record_sent(j, ids.get(key), today)
    for j in overflow:
        sub.mark_seen(j.url or job_key(j), today)
    sub.prune(today)
    return len(ids)
