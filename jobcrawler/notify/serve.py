"""The multi-user run: read what people typed, crawl, send each their own.

    jobcrawler-serve --dry-run     # crawl and report, send nothing
    jobcrawler-serve               # the real thing

Ordering matters and is the same argument the single-user bot makes. Updates
are read first, so someone who typed /start this morning is subscribed before
this morning's postings go out, and a company muted this morning does not
reappear in it. Subscriber state is written before the batch is acknowledged,
so a crash loses a re-read rather than a tap.
"""

import argparse
import os
import sys
from datetime import datetime

from ..filters.countries import COUNTRIES, country_codes
from ..report.events import Reporter
from .fanout import crawl_country, deliver, for_subscriber
from .router import chat_of, handle_command, handle_setup, is_block
from .secrets import load_env_file, redact
from .subscribers import Subscribers
from .telegram import Blocked, TelegramError, TelegramNotifier

# The same set the single-reader bot crawls: every documented API needing no
# key, plus Adzuna for salary, plus the two scrapes.
SERVE_SOURCES = ["greenhouse", "ashby", "lever", "workable", "smartrecruiters",
                 "himalayas", "remotive", "remoteok", "arbeitnow", "wwr", "hn",
                 "adzuna", "linkedin", "builtin"]


def parser():
    p = argparse.ArgumentParser(
        prog="jobcrawler-serve",
        description="Crawl once per country, send each subscriber their own feed.")
    p.add_argument("--subscribers", default="subscribers.json",
                   help="the subscriber file (default subscribers.json)")
    p.add_argument("--country", nargs="+", default=country_codes(),
                   choices=country_codes(),
                   help="which countries to crawl (default: all of them)")
    p.add_argument("--source", nargs="+", default=SERVE_SOURCES, metavar="NAME")
    p.add_argument("-d", "--days", type=int, default=14)
    p.add_argument("-p", "--pages", type=int, default=3)
    p.add_argument("--delay", type=float, default=4.0,
                   help="seconds between LinkedIn requests (default 4)")
    p.add_argument("--max-messages", type=int, default=15, metavar="N",
                   help="individual messages per subscriber before the rest "
                        "become one summary (default 15)")
    p.add_argument("--updates-only", action="store_true",
                   help="read and answer messages, then stop — no crawl. "
                        "For running between the scheduled crawls so /start "
                        "is answered sooner than twice a day")
    p.add_argument("--dry-run", action="store_true",
                   help="crawl and report what each subscriber would get")
    p.add_argument("-q", "--quiet", action="store_true")
    return p


def read_updates(bot, subs, report):
    """Answer everything people have typed or tapped since the last run."""
    try:
        batch = bot.updates(offset=None)
    except TelegramError as e:
        report.warn(f"  ! telegram: could not read updates: {e}")
        return 0

    handled, last_id = 0, None
    for update in batch:
        last_id = update.get("update_id", last_id)
        chat_id = chat_of(update)
        if not chat_id:
            continue

        if is_block(update):
            if subs.remove(chat_id):
                report.line(f"  {chat_id} left — unsubscribed")
            continue

        query = update.get("callback_query")
        if query:
            data = query.get("data") or ""
            bot.last_message_id[chat_id] = (
                (query.get("message") or {}).get("message_id"))
            if handle_setup(bot, subs, chat_id, data, query.get("id")):
                bot.answer_raw(query.get("id"), "Saved")
                handled += 1
            continue

        text = (update.get("message") or {}).get("text")
        if text and handle_command(bot, subs, chat_id, text):
            handled += 1

    if last_id is not None:
        bot.acknowledge(last_id)
    return handled


def main(argv=None):
    args = parser().parse_args(argv)
    load_env_file()
    report = Reporter(quiet=args.quiet)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        report.warn("! TELEGRAM_BOT_TOKEN is not set")
        return 2

    # chat_id is required by the constructor and unused by a fan-out, which
    # addresses every send explicitly. Any id satisfies it.
    bot = TelegramNotifier(token, "0", report=report)
    try:
        who = bot.check()
    except TelegramError as e:
        report.warn(f"! telegram unavailable: {e}")
        return 2

    subs = Subscribers(args.subscribers).load()
    report.line(f"telegram: @{who} · {len(subs)} subscriber"
                f"{'' if len(subs) == 1 else 's'} (token {redact(token)})")

    # Before the crawl: someone who typed /start this morning should be in
    # this morning's send, and a company muted this morning should not be.
    handled = read_updates(bot, subs, report)
    if handled:
        report.line(f"{handled} message{'' if handled == 1 else 's'} answered")
    subs.save()

    if args.updates_only:
        return 0

    active = subs.active()
    if not active:
        report.result("no active subscribers — nothing to crawl for")
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    wanted = {s.country for s in active} & set(args.country)
    sent_total = 0

    for code in sorted(wanted):
        jobs, cfg = crawl_country(code, args.source, args.days, args.pages,
                                  report, args.delay)
        country = COUNTRIES[code]
        for sub in [s for s in active if s.country == code]:
            batch = for_subscriber(sub, jobs, today)
            if not batch:
                report.line(f"  {sub.chat_id}: nothing new")
                continue
            if args.dry_run:
                report.result(f"  {sub.chat_id}: would send {len(batch)} "
                              f"({', '.join(sub.roles)})")
                continue
            n = deliver(bot, subs, sub, batch, country, today,
                        args.max_messages, report)
            sent_total += n
            report.line(f"  {sub.chat_id}: sent {n} of {len(batch)}")

    if not args.dry_run:
        subs.save()
    report.result(f"\n{sent_total} message{'' if sent_total == 1 else 's'} "
                  f"to {len(active)} subscriber"
                  f"{'' if len(active) == 1 else 's'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
