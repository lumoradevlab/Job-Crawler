"""The command line: parse the flags, then run the pipeline."""

import argparse
import sys
from datetime import datetime

from .filters.geo import us_status
from .filters.rules import rejection
from .net import http
from .net.ratelimit import HostPolicy
from .models import row  # noqa: F401  (kept on the module for the shim)
from .pipeline.dedupe import SOURCE_RANK, dedupe_key
from .report.writers import (SALARY_FIELDS, report_rejections,
                             strip_bookkeeping, write_outputs)
from .sources.ats.discover import discover_boards
from .sources.linkedin import EXPERIENCE
from .sources.registry import DEFAULT_SOURCES, SOURCES
from .store.archive import append_archive, load_archive
from .store.seen import META, catchup_days, job_key, load_state, save_state


DEFAULT_QUERIES = [
    "Android Developer",
    "Android Engineer",
    "Mobile Developer",
    "Mobile Engineer",
    "Android Software Engineer",
    "Kotlin Developer",
    "Mobile Software Engineer",
    "Senior Android Developer",
]


def report_network(stats, kept):
    """Say what the network did, so an empty run is never ambiguous.

    A crawler that reports "0 jobs" after every single request failed is
    stating a fact about the job market it has no evidence for. The blackout
    line exists because that exact wrong answer cost an afternoon: a Mac with
    no CA bundle failed 46 of 46 requests and reported a quiet week.
    """
    if not stats.requests:
        return
    if stats.total_blackout():
        kinds = ", ".join(f"{k} x{n}" for k, n in
                          sorted(stats.by_kind().items(), key=lambda kv: -kv[1]))
        print(f"\n  !! every request failed: {stats.requests} of "
              f"{stats.requests} ({kinds})", file=sys.stderr)
        print("     the run reached no board at all, so '0 jobs' above says "
              "nothing about what is out there.", file=sys.stderr)
        if "SSLCertVerificationError" in stats.by_kind():
            print("     no CA certificates: run the Install Certificates "
                  "command that ships with python.org Python, or set "
                  "SSL_CERT_FILE.", file=sys.stderr)
    elif stats.failed:
        hosts = ", ".join(f"{h} {n}" for h, n in
                          sorted(stats.by_host().items(), key=lambda kv: -kv[1])[:3])
        print(f"  {stats.failed} of {stats.requests} requests failed "
              f"({hosts})" + ("; some boards were not read" if not kept else ""))


def main():
    p = argparse.ArgumentParser(
        description="Crawl remote Android/mobile jobs across every reachable "
                    "board. US-only unless told otherwise.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  # every working source, remote in the US, last 60 days;
  # later runs only catch up on what's new
  python3 crawler.py

  # strict US — drop "Worldwide"/"Anywhere" and unlabelled postings
  python3 crawler.py --strict-us

  # deep sweep with Easy Apply flags (slow: one request per LinkedIn job)
  python3 crawler.py --days 30 --pages 10 --details

  # company ATS boards only — the highest-signal source
  python3 crawler.py --source greenhouse ashby
  python3 crawler.py --source greenhouse --boards stripe figma discord
  python3 crawler.py --source ashby --ashby-boards notion strava plaid

  # skip LinkedIn entirely
  python3 crawler.py --source greenhouse arc wwr hn

  # only the LinkedIn Easy Apply jobs
  python3 crawler.py --source linkedin --details --easy-apply-only

  # your own queries, drop lead/manager titles
  python3 crawler.py -k "Kotlin Developer" "Compose Developer" \\
      --exclude "tech lead" manager

  # why can't it read Indeed?
  python3 crawler.py --source indeed

  # go worldwide again
  python3 crawler.py --anywhere
""",
    )
    p.add_argument("-k", "--keywords", nargs="+", default=DEFAULT_QUERIES,
                   metavar="QUERY",
                   help="search queries (default: %d mobile/Android variants)"
                        % len(DEFAULT_QUERIES))
    p.add_argument("--source", nargs="+", default=DEFAULT_SOURCES,
                   choices=sorted(SOURCES),
                   help="default: " + " ".join(DEFAULT_SOURCES))
    p.add_argument("-l", "--location", default="United States",
                   help='LinkedIn location (default "United States"; '
                        '"Worldwide" and "USA" are also resolved exactly)')
    p.add_argument("-p", "--pages", type=int, default=5,
                   help="LinkedIn pages per query, 10 jobs each (default 5)")
    p.add_argument("-d", "--days", type=int, default=60,
                   help="only postings from the last N days (0 = no limit). "
                        "After the first run the window shrinks to the gap "
                        "since that run — see --full")
    p.add_argument("--full", action="store_true",
                   help="re-sweep the whole --days window instead of only "
                        "catching up since the last run")
    p.add_argument("--level", choices=sorted(EXPERIENCE),
                   help="experience level filter (LinkedIn only)")
    p.add_argument("--boards", nargs="+", metavar="TOKEN",
                   help="override the Greenhouse company list")
    p.add_argument("--ashby-boards", nargs="+", metavar="TOKEN",
                   help="override the Ashby company list")
    p.add_argument("--lever-boards", nargs="+", metavar="TOKEN",
                   help="override the Lever company list")
    p.add_argument("--workable-boards", nargs="+", metavar="TOKEN",
                   help="override the Workable company list")
    p.add_argument("--sr-boards", nargs="+", metavar="TOKEN",
                   help="override the SmartRecruiters company list")
    p.add_argument("--discover", action="store_true",
                   help="after crawling, probe every company name found "
                        "against all five ATSes and remember the boards that "
                        "answer; they join the lists from the next run on")
    p.add_argument("--min-salary", type=int, metavar="N",
                   help="drop postings whose stated pay is below N "
                        "(postings that state no pay are kept)")
    p.add_argument("--details", action="store_true",
                   help="fetch each LinkedIn posting: description + Easy Apply")
    p.add_argument("--easy-apply-only", action="store_true",
                   help="keep only LinkedIn Easy Apply jobs (needs --details)")
    p.add_argument("--must", nargs="+", metavar="WORD",
                   help="keep only jobs containing all of these words")
    p.add_argument("--exclude", nargs="+", metavar="WORD",
                   help="drop jobs whose title contains any of these")
    p.add_argument("--why", action="store_true",
                   help="explain the rejections: a breakdown by rule and "
                        "source, and <out>_rejected.csv naming the rule "
                        "that dropped each posting")
    p.add_argument("--no-filter", action="store_true",
                   help="skip the Android/mobile title gate, keep every hit")
    p.add_argument("--strict-us", action="store_true",
                   help="require the posting to name the US; drops "
                        '"Worldwide"/"Anywhere" and unlabelled listings')
    p.add_argument("--anywhere", action="store_true",
                   help="turn the US gate off entirely (worldwide results)")
    p.add_argument("--delay", type=float, default=4.0,
                   help="seconds between LinkedIn requests (default 4)")
    p.add_argument("-o", "--out", default="android_remote_jobs",
                   help="output basename (.csv, .json and _links.txt)")
    p.add_argument("--no-archive", action="store_true",
                   help="do not append this run's matches to "
                        "<out>_archive.jsonl (the archive is the only full "
                        "record; the .csv holds just this run)")
    p.add_argument("--replay", action="store_true",
                   help="rebuild the outputs from <out>_archive.jsonl instead "
                        "of crawling — re-filter everything ever matched with "
                        "no network at all, e.g. --replay --days 30")
    p.add_argument("--include-seen", action="store_true",
                   help="report every match, not just ones new since the "
                        "last run")
    p.add_argument("--reset-seen", action="store_true",
                   help="forget the run history and start counting again")
    p.add_argument("--state", metavar="FILE",
                   help="seen-job history file (default <out>_seen.json)")
    args = p.parse_args()
    state_path = args.state or (args.out + "_seen.json")

    # --anywhere with the default location would still pin LinkedIn to the US.
    if args.anywhere and args.location == "United States":
        args.location = "Worldwide"

    # --delay is LinkedIn's pacing and always was; it now reaches the request
    # layer as that host's policy instead of a sleep at the bottom of a loop.
    http.DEFAULT.limiter.set_policy(
        "www.linkedin.com", HostPolicy(gap=args.delay, jitter=1.5))

    # Load the history before crawling, so the sources can narrow their own
    # work: a shorter date window, and no detail fetches for known jobs.
    state = {} if args.reset_seen else load_state(state_path)
    today = datetime.now().strftime("%Y-%m-%d")

    # Boards --discover has found on earlier runs, merged into the built-in
    # lists by board_list() before any ATS source starts listing.
    boards_path = args.out + "_boards.json"
    boards = load_state(boards_path)
    args.boards_found = boards.get("found", {})
    grown = sum(len(v) for v in args.boards_found.values())
    if grown:
        print(f"{grown} discovered board{'' if grown == 1 else 's'} "
              f"from {boards_path}")
    args.seen_keys = set() if args.include_seen else {
        k for k in state if k != META
    }

    # A replay re-filters what is already on disk; narrowing the window to
    # "since the last run" would silently hide almost all of it.
    if not args.full and args.days and not args.replay:
        narrowed = catchup_days(state, args.days, today)
        if narrowed != args.days:
            print(f"catching up: asking for the last {narrowed} days instead "
                  f"of {args.days} (last run {state[META]['last_run']}); "
                  f"--full re-sweeps the whole window")
            args.days = narrowed

    archive_path = args.out + "_archive.jsonl"

    collected = []
    if args.replay:
        # Re-filtering what is already on disk, so every gate still applies
        # but nothing is fetched. Reporting only what is "new" would return
        # nothing at all here, since the archive is by definition seen.
        collected = load_archive(archive_path)
        args.include_seen = True
        args.seen_keys = set()
        print(f"replaying {len(collected)} archived postings from "
              f"{archive_path} — nothing will be fetched")
    else:
        for name in args.source:
            args.source_now = name      # tags what relevant() turns away
            try:
                collected.extend(SOURCES[name](args))
            except KeyboardInterrupt:
                print("\ninterrupted — writing what we have")
                break
            except Exception as e:              # keep other sources alive
                print(f"  ! {name} failed: {type(e).__name__}: {e}",
                      file=sys.stderr)

    # Every result names a company, and a company name is a candidate ATS
    # slug — so this run's aggregator hits become next run's board list.
    if args.discover and not args.replay:
        names = [j["company"] for j in collected]
        names += [v.get("company", "") for k, v in state.items()
                  if k != META and isinstance(v, dict)]
        discover_boards(names, boards, today)
        boards[META] = {"last_run": today}
        save_state(boards_path, boards)

    # Two sources describing one job is common now that aggregators are in
    # the mix, and the first one crawled is not the one worth keeping: a
    # company's own ATS link outlives the aggregator's redirect and states
    # its location honestly. So collapse on (title, company) by RANK, not by
    # arrival order.
    best, rejected = {}, []
    for j in collected:
        why = rejection(j, args)
        if why:
            if args.why:
                rejected.append((j, why))
            continue
        j["us"] = j.get("us") or us_status(j.get("location", ""))
        key = dedupe_key(j)
        prior = best.get(key)
        if prior is None or SOURCE_RANK.get(j["source"], 50) < \
                SOURCE_RANK.get(prior["source"], 50):
            # Keep any salary the loser knew and the winner doesn't.
            if prior and prior.get("salary_min") and not j.get("salary_min"):
                for f in SALARY_FIELDS:
                    j[f] = prior[f]
            if prior is not None and args.why:
                rejected.append((prior, "duplicate: %s carries the same job "
                                        "on a better link" % j["source"]))
            best[key] = j
        else:
            if prior.get("salary_min") is None and j.get("salary_min"):
                for f in SALARY_FIELDS:
                    prior[f] = j[f]
            if args.why:
                rejected.append((j, "duplicate: kept the %s record instead"
                                    % prior["source"]))
    jobs = list(best.values())

    if args.min_salary:
        jobs = [j for j in jobs
                if not j.get("salary_max")
                or (j.get("salary_max") or 0) >= args.min_salary]
    jobs.sort(key=lambda j: (j.get("posted") or "", j["source"]), reverse=True)

    # Split into new vs already-reported, then remember everything we saw.
    fresh = []
    for j in jobs:
        prior = state.get(job_key(j))
        j["first_seen"] = (prior or {}).get("first_seen", today)
        if prior is None:
            fresh.append(j)
    total_matched = len(jobs)

    # Everything matched, before the split below narrows the report to what
    # is new — that split is a reporting choice, not a reason to lose data.
    archived = 0
    if not (args.no_archive or args.replay):
        archived = append_archive(archive_path, strip_bookkeeping(jobs))

    if not args.replay:
        for j in jobs:
            state[job_key(j)] = {"first_seen": j["first_seen"],
                                 "title": j["title"], "company": j["company"]}
        state[META] = {"last_run": today, "window_days": args.days}
        save_state(state_path, state)

    if not args.include_seen:
        jobs = fresh

    write_outputs(jobs, args.out)

    by_source = {}
    for j in jobs:
        by_source[j["source"]] = by_source.get(j["source"], 0) + 1

    seen_before = total_matched - len(fresh)
    print(f"\n{len(jobs)} jobs -> {args.out}.csv / .json / _links.txt")
    print(f"  {len(fresh)} new since the last run, {seen_before} already "
          f"reported ({total_matched} matched in total)")
    if archived:
        print(f"  {archived} appended to {archive_path}")
    if by_source:
        print("  " + ", ".join(f"{k}: {v}" for k, v in sorted(by_source.items())))
    if jobs and not args.anywhere:
        named = sum(1 for j in jobs if j["us"] == "us")
        print(f"  {named} name a US location, {len(jobs) - named} are "
              f'"worldwide"/unlabelled (use --strict-us to drop those)')
    if args.details:
        print(f"  {sum(1 for j in jobs if j['easy_apply'] == 'yes')} are Easy Apply")
    report_network(http.DEFAULT.stats, len(jobs))
    if args.why:
        report_rejections(rejected, args.out)
    print()

    for j in jobs:
        tag = {"yes": "[easy]", "no": "[site]"}.get(j["easy_apply"], "")
        print(f"{j['source']:<11}{(j['posted'] or '?'):<12}"
              f"{j['title'][:42]:<44}{j['company'][:18]:<20}{tag:<7}{j['url']}")
