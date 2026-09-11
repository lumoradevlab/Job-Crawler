"""The Telegram bot: one crawl, then the new postings sent to a chat.

Deliberately a wrapper around the ordinary run rather than a parallel one.
Everything that decides what a "new job" is — the title gate, the US rules,
the dedupe, the seen-state — already exists and is tested; a bot that made
any of those decisions again would be a second implementation to keep in
step with the first. So this runs the pipeline, takes what split_new()
returns, and sends that.

Ordering matters here and is the one thing worth reading twice. The state
file is written only *after* Telegram has accepted the messages. Written
first, a failed send would mark the jobs as reported and they would never be
sent again — the bot would go quiet and look healthy. Written after, a failed
send means the next run finds the same jobs still new and tries once more.
The cost is a possible duplicate if the process dies between sending and
saving, and a duplicate posting is a far better failure than a silent one.
"""

import argparse
import os
import sys
from datetime import datetime

from ..config import CrawlConfig, FilterConfig
from ..context import RunContext
from ..net.http import Fetcher
from ..net.ratelimit import HostPolicy, RateLimiter
from ..pipeline.collect import collect
from ..pipeline.select import select, split_new
from ..report.events import Reporter
from ..roles import DEFAULT_ROLE, combine, profile_names
from ..sources.registry import SOURCES
from ..store.archive import Archive
from ..store.seen import (catchup_days, job_key, load_state, record_run,
                          save_state)
from .secrets import load_env_file, redact
from .taps import drain, muted_companies, suppressed
from .telegram import TelegramError, TelegramNotifier

# The schedule's sources: every documented API needing no key, plus Adzuna
# for its salary data, plus LinkedIn and Built In.
#
# Those last two are HTML scrapes rather than APIs, which is a real
# difference and worth stating. An API changes behind a version; a scrape
# changes whenever the site's markup does, and the failure is silent — the
# patterns simply stop matching and the source reports zero. collect()
# isolates that (a broken source cannot take the run down) and its clock is
# not advanced (so its window is still there when it is fixed), but nothing
# can make a scrape as durable as an endpoint with a contract.
#
# LinkedIn additionally throttles by IP, and an Actions runner shares its
# address with everything else GitHub runs — so expect it to return less
# there than it does from a laptop. That is throttling, not breakage.
#
# Still absent: serpapi, whose free tier is 250 searches a MONTH and would
# not survive a fortnight of twice-daily runs at 8 keywords; and arc, which
# is a third scrape whose postings overlap heavily with what is already here.
# Both can still be asked for explicitly with --source.
BOT_SOURCES = [
    "greenhouse", "ashby", "lever", "workable", "smartrecruiters",
    "himalayas", "remotive", "remoteok", "arbeitnow", "wwr", "hn",
    "adzuna", "linkedin", "builtin",
]

# Kept as a name for anything importing it; the queries a run actually uses
# come from its --role profile.
DEFAULT_QUERIES = list(combine([DEFAULT_ROLE]).queries)

# Sending 40 messages because someone deleted the state file is how a bot
# gets muted. Past this many, the run says so and sends nothing rather than
# flooding the chat; --max-messages 0 turns the guard off.
DEFAULT_MAX_MESSAGES = 25


def parser():
    p = argparse.ArgumentParser(
        prog="jobcrawler-bot",
        description="Crawl, then post any new jobs to a Telegram chat.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
credentials (environment variables, or a git-ignored .env beside this repo):
  TELEGRAM_BOT_TOKEN   from @BotFather on Telegram
  TELEGRAM_CHAT_ID     the chat to post into; --chat-id-help explains how
  ADZUNA_APP_ID        optional, free at developer.adzuna.com/signup
  ADZUNA_APP_KEY       optional, the key for that app id

examples:
  jobcrawler-bot --dry-run        # crawl and print, send nothing
  jobcrawler-bot --check          # verify the token and chat, then stop
  jobcrawler-bot                  # the real thing, as the schedule runs it
""")
    p.add_argument("--role", nargs="+", default=[DEFAULT_ROLE],
                   choices=profile_names(), metavar="NAME",
                   help="which discipline to send jobs for (default %s). "
                        "Several may be given: --role backend devops. "
                        "Names: %s"
                        % (DEFAULT_ROLE, ", ".join(profile_names())))
    p.add_argument("-k", "--keywords", nargs="+", metavar="QUERY",
                   default=None,
                   help="override the role's default search queries")
    p.add_argument("--source", nargs="+", choices=sorted(SOURCES),
                   default=BOT_SOURCES, metavar="NAME",
                   help="default: every keyless API plus adzuna")
    p.add_argument("-d", "--days", type=int, default=14,
                   help="only postings from the last N days (default 14); "
                        "after the first run the window shrinks to the gap "
                        "since it")
    p.add_argument("-p", "--pages", type=int, default=3,
                   help="pages per query where a source pages (default 3)")
    p.add_argument("--delay", type=float, default=4.0, metavar="SECONDS",
                   help="seconds between LinkedIn requests (default 4). "
                        "LinkedIn throttles by IP; lowering this is what "
                        "gets a run half-empty")
    p.add_argument("--strict-us", action="store_true",
                   help="require the posting to name the US")
    p.add_argument("--anywhere", action="store_true",
                   help="turn the US gate off entirely")
    p.add_argument("--min-salary", type=int, metavar="N",
                   help="drop postings whose stated pay is below N")
    p.add_argument("--exclude", nargs="+", metavar="WORD",
                   help="drop jobs whose title contains any of these")
    p.add_argument("-o", "--out", default="android_remote_jobs",
                   help="output basename, shared with the main crawler")
    p.add_argument("--state", metavar="FILE",
                   help="seen-job history file (default <out>_seen.json)")
    p.add_argument("--max-messages", type=int, default=DEFAULT_MAX_MESSAGES,
                   metavar="N",
                   help="refuse to send more than N in one run, so a lost "
                        "state file cannot flood the chat (0 = no limit)")
    p.add_argument("--no-digest", action="store_true",
                   help="over --max-messages, refuse to send rather than "
                        "summarising the overflow. The old behaviour, kept "
                        "for the case the cap was written for: a lost state "
                        "file, where stopping is the right answer")
    p.add_argument("--newest", action="store_true",
                   help="when there are more than --max-messages, send the "
                        "newest N instead of refusing. The rest are marked "
                        "seen, not lost — they stay in the archive. This is "
                        "the flag for adding a source, where a large batch "
                        "is a real backfill rather than a lost state file")
    p.add_argument("--no-buttons", action="store_true",
                   help="send plain messages with no Applied/Save/Mute "
                        "buttons, and do not read taps")
    p.add_argument("--dry-run", action="store_true",
                   help="crawl and print what would be sent, send nothing")
    p.add_argument("--check", action="store_true",
                   help="verify the credentials and exit without crawling")
    p.add_argument("--chat-id-help", action="store_true",
                   help="explain how to find your TELEGRAM_CHAT_ID")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="print only results and warnings")
    return p


CHAT_ID_HELP = """
Finding your TELEGRAM_CHAT_ID
-----------------------------
1. In Telegram, message @BotFather and send /newbot. Follow the prompts.
   It replies with a token like 8123456789:AAH...  -> TELEGRAM_BOT_TOKEN

2. Send your new bot any message ("hi") from the chat you want jobs in.
   A bot cannot message you first; this is what opens the conversation.

3. Ask the API who has talked to it:

     curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"

   In the reply find  "chat":{"id":123456789  -> TELEGRAM_CHAT_ID
   A personal chat id is positive; a group's is negative (-100...).

4. For a group: add the bot to it, send a message there, and re-run the
   curl above. Group ids start with -100.

Never commit these. Put them in .env locally (it is git-ignored), and in
GitHub Actions store them under Settings -> Secrets and variables -> Actions.
"""


def build(args, report, days, today, state):
    """The two objects every source is handed. No argparse past this point."""
    profile = combine(args.role)
    filters = FilterConfig(
        subject=profile.pattern(),
        role=profile.role_pattern(),
        exclude=tuple(args.exclude) if args.exclude else None,
        anywhere=args.anywhere,
        strict_us=args.strict_us,
        days=days,
        min_salary=args.min_salary,
    )
    cfg = CrawlConfig(
        keywords=tuple(args.keywords or profile.queries),
        sources=tuple(args.source),
        location="Worldwide" if args.anywhere else "United States",
        pages=args.pages,
        days=days,
        delay=args.delay,
        filters=filters,
    )
    # RateLimiter() with no arguments carries DEFAULT_POLICIES, which is
    # where LinkedIn's several-second gap lives. Passing a default= here
    # instead — as this did — replaced the fallback for unnamed hosts and
    # left the named ones alone, but it read like "no pacing anywhere" and
    # would have become exactly that the moment someone passed policies=.
    # With LinkedIn now on the schedule the pacing is load-bearing, so it is
    # built the way cli.py builds it, and --delay moves the same knob.
    limiter = RateLimiter()
    limiter.set_policy("www.linkedin.com",
                       HostPolicy(gap=args.delay, jitter=1.5))
    ctx = RunContext(fetch=Fetcher(limiter=limiter, report=report),
                     report=report, today=today,
                     seen_keys={k for k in state if not k.startswith("_")})
    return cfg, ctx


def main(argv=None):
    args = parser().parse_args(argv)

    if args.chat_id_help:
        print(CHAT_ID_HELP)
        return 0

    load_env_file()
    report = Reporter(quiet=args.quiet)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    notifier = None
    if not args.dry_run:
        try:
            notifier = TelegramNotifier(token, chat_id, report=report)
            who = notifier.check()
            report.line(f"telegram: @{who} -> chat {chat_id} "
                        f"(token {redact(token)})")
        except TelegramError as e:
            report.warn(f"! telegram unavailable: {e}")
            report.warn("  run --chat-id-help for how to get these")
            return 2
    if args.check:
        return 0

    state_path = args.state or (args.out + "_seen.json")
    state = load_state(state_path)
    today = datetime.now().strftime("%Y-%m-%d")

    # Before the crawl, never after: a company muted this morning must not
    # reappear in this morning's results. State is saved immediately so a
    # crash between here and the send cannot lose a tap that Telegram has
    # already been told we handled.
    if notifier is not None and not args.no_buttons:
        applied, _ = drain(notifier, state, today, report)
        if applied:
            save_state(state_path, state)

    # The same catch-up the crawler does: after the first sweep there is no
    # reason to ask for 14 days again, only for what appeared since.
    days = catchup_days(state, args.days, today, args.source)
    cfg, ctx = build(args, report, days, today, state)

    outcome = collect(cfg, ctx, SOURCES)
    jobs, _ = select(outcome.postings, cfg.filters)

    # A muted company is dropped from the report but stays in the archive:
    # muting is a statement about what to be shown, not about what happened
    # in the market, and --replay must still be able to rebuild the record.
    muted = muted_companies(state)
    turned_down = suppressed(state)
    if muted or turned_down:
        before = len(jobs)
        jobs = [j for j in jobs
                if j.company.strip().lower() not in muted
                and job_key(j) not in turned_down]
        if before != len(jobs):
            report.line(f"{before - len(jobs)} hidden by your taps "
                        f"({len(muted)} muted compan"
                        f"{'y' if len(muted) == 1 else 'ies'})")

    fresh = split_new(jobs, state, today)

    # Archived before anything is sent: the archive is the only full record,
    # and a Telegram failure is no reason to lose the crawl.
    archive = Archive(args.out + "_archive.jsonl")
    archive.add(jobs)

    report.result(f"\n{len(jobs)} matched, {len(fresh)} new since the last run")

    if not fresh:
        # Still record the run: the sources that worked have been asked for
        # this window, and not advancing them means re-asking tomorrow.
        record_run(state, today, cfg.days, outcome.succeeded)
        save_state(state_path, state)
        report.result("nothing new to send")
        return 0

    over = args.max_messages and len(fresh) > args.max_messages

    # --no-digest restores the old behaviour: refuse outright. Kept because
    # a genuinely lost state file is still worth stopping for, and only the
    # reader knows which of the two they are looking at.
    if over and args.no_digest and not args.newest:
        report.warn(f"! {len(fresh)} new postings exceeds --max-messages "
                    f"{args.max_messages} — sending none.")
        report.warn("  drop --no-digest to send the newest "
                    f"{args.max_messages} plus a summary of the rest, or "
                    "--max-messages 0 to send them all individually.")
        return 3

    digest = []
    if over:
        # select() has already sorted by date, newest first. The tail is
        # summarised rather than dropped: with fourteen sources a busy day
        # legitimately turns up forty jobs, and losing thirty of them
        # silently is worse than the flood the cap was guarding against.
        digest = fresh[args.max_messages:]
        fresh = fresh[:args.max_messages]
        report.line(f"sending the newest {len(fresh)} individually, "
                    f"{len(digest)} more in a summary")

    if args.dry_run:
        from .telegram import format_posting
        for j in fresh:
            report.result("-" * 60)
            report.result(format_posting(j))
        if digest:
            from .telegram import format_digest
            report.result("-" * 60)
            report.result(format_digest(digest, len(fresh)))
        report.result("-" * 60)
        report.result(f"{len(fresh)} message(s) would be sent"
                      + (f", plus a summary of {len(digest)}" if digest else "")
                      + ". Nothing was sent and no state was written.")
        return 0

    ids = notifier.send_postings(fresh, buttons=not args.no_buttons,
                                 key_of=job_key)
    sent = len(ids)
    if digest:
        from .telegram import format_digest
        try:
            notifier.send(format_digest(digest, sent))
            report.result(f"sent {sent} individually, {len(digest)} in a summary")
        except TelegramError as e:
            # The summary failing must not cost the state write: the
            # individual messages already arrived, and re-sending those
            # tomorrow would be worse than losing one summary.
            report.warn(f"  ! telegram: summary failed: {e}")
    else:
        report.result(f"sent {sent} of {len(fresh)} to telegram")

    # Only now. A job is "reported" when Telegram has it, not when we decided
    # to send it — see the module docstring.
    if sent:
        for j in jobs:
            key = job_key(j)
            entry = state.get(key) or {}
            entry.update({"first_seen": j.first_seen, "title": j.title,
                          "company": j.company})
            # Only for what was actually sent: the id is what a later tap
            # edits, and a job we never messaged has no message to edit.
            if key in ids:
                entry["message_id"] = ids[key]
                entry.setdefault("state", "sent")
            state[key] = entry
        record_run(state, today, cfg.days, outcome.succeeded)
        save_state(state_path, state)
    else:
        report.warn("! nothing was sent, so the state file was left alone — "
                    "the next run will try these again")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
