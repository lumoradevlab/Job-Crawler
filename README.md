# Remote Android/Mobile job crawler

Collects **remote-in-the-USA** Android, Kotlin and mobile engineering job links
from seven job boards in one pass. Python 3 stdlib only — nothing to install.

```bash
cd ~/remote-job-crawler
python3 crawler.py                    # 8 default queries, remote US, last 60 days
```

Three files are written each run:

| file | contents |
|---|---|
| `android_remote_jobs_links.txt` | just the URLs, one per line |
| `android_remote_jobs.csv` | open in Excel/Numbers — title, company, location, date, Easy Apply, link |
| `android_remote_jobs.json` | same data for scripting |

## Two behaviours worth knowing first

Both make a run look emptier than it is, and both are deliberate:

1. **Only new jobs are reported.** After the first run, anything already shown
   is held back. `--include-seen` switches that off.
2. **The date window shrinks itself.** After a 60-day sweep, the next run only
   asks for the days since — so running twice in one day asks for ~2 days, and
   the low-volume boards return almost nothing. `--full` forces the whole window.

```bash
# everything the crawler has ever matched
python3 crawler.py --include-seen --full

# the past week, up to today
python3 crawler.py --days 7 --full --include-seen
```

## Sources

| source | what it is |
|---|---|
| `linkedin` | public *guest* search endpoint, no login — by far the highest volume |
| `greenhouse` | 44 company ATS boards via the public Greenhouse job-board API |
| `ashby` | 35 company ATS boards via the public Ashby posting API |
| `builtin` | builtin.com remote engineering board |
| `arc` | arc.dev remote board |
| `wwr` | We Work Remotely RSS feeds |
| `hn` | Hacker News "Ask HN: Who is hiring?" via the official HN API |
| `remoteok`, `arbeitnow` | free public APIs — work, but off by default |

The two ATS sources are the highest-signal ones: results link straight to the
company's own careers page rather than an aggregator.

**Not available:** `indeed`, `wellfound` and `dice` block automated access,
`hired` shut down, and Remotive's API now returns only 16 jobs in total. Asking
for one of them prints the reason and returns nothing.

```bash
# company career boards only
python3 crawler.py --source greenhouse ashby --full --include-seen

# specific companies
python3 crawler.py --source ashby --ashby-boards notion strava plaid
python3 crawler.py --source greenhouse --boards stripe figma discord
```

## Common runs

```bash
# deep sweep — last 30 days, 10 pages per query, with Easy Apply flags
python3 crawler.py --days 30 --pages 10 --details

# only the LinkedIn Easy Apply jobs
python3 crawler.py --source linkedin --details --easy-apply-only

# strict US — drop "Worldwide"/"Anywhere" and unlabelled postings
python3 crawler.py --strict-us

# turn the US gate off and go worldwide again
python3 crawler.py --anywhere

# all remote engineering jobs, not just Android
python3 crawler.py --no-filter -k "Software Engineer" "Developer"

# your own queries, drop lead/manager roles
python3 crawler.py -k "Kotlin Developer" "Compose Developer" \
    --exclude "tech lead" manager principal
```

## Options

| flag | meaning |
|---|---|
| `-k, --keywords` | search queries (default: 8 mobile/Android variants) |
| `--source` | which boards to crawl (default: linkedin greenhouse ashby builtin arc wwr hn) |
| `-l, --location` | LinkedIn location, default `United States` |
| `-p, --pages` | pages per query, 10 jobs each (default 5) |
| `-d, --days` | only postings from the last N days, `0` = no limit (default 60) |
| `--full` | re-sweep the whole window instead of catching up since the last run |
| `--level` | `entry` / `mid` / `senior` / `director` … (LinkedIn only) |
| `--boards` | override the Greenhouse company list |
| `--ashby-boards` | override the Ashby company list |
| `--details` | fetch each LinkedIn posting: description + Easy Apply flag |
| `--easy-apply-only` | keep only Easy Apply jobs (requires `--details`) |
| `--must` / `--exclude` | keyword filters |
| `--no-filter` | keep every hit, skip the Android/mobile title gate |
| `--strict-us` | require the posting to name the US (drops worldwide/unlabelled) |
| `--anywhere` | switch the US gate off entirely |
| `--delay` | seconds between LinkedIn requests, default 4 — **don't lower this** |
| `-o, --out` | output basename, default `android_remote_jobs` |
| `--include-seen` | report every match, not just new ones |
| `--reset-seen` | forget the run history and start again |
| `--state` | history file, default `<out>_seen.json` |

## How it grades a posting

Every source is normalised into one record and put through the same gate: an
Android/mobile title, a genuine remote flag, and a US check.

The US check is the fiddly part, because boards state it in free text. A
posting fenced to another region is dropped; "Worldwide"/"Anywhere" is kept (a
US applicant qualifies); `--strict-us` keeps only postings that name the US.
Ashby is the one source that states the country outright — everywhere else it
is read out of prose, so the location column is worth a glance.

A bare "remote" in a job body is ignored deliberately: boilerplate like "if the
role can be performed remotely" appears in strictly on-site postings, so only
committed phrasings ("fully remote", "US-Remote") count when the location field
itself is silent.

## Notes and limits

- **Automated scraping is against LinkedIn's Terms of Service.** This hits only
  public, logged-out pages at a slow rate, but it's your account/IP at risk if
  you hammer it. Keep `--delay` at 4s or higher and prefer more queries over
  more pages.
- If you start getting `HTTP 429`, stop for an hour. The script backs off and
  retries automatically, but repeated 429s mean you're being throttled.
- **LinkedIn's remote filter leaks, and this is the weakest part of the crawl.**
  Roles titled "(Hybrid)" and "- Onsite" come back inside `f_WT=2`, and the
  guest pages carry no workplace-type field to check against. Titles and
  bodies that name a workplace are now rejected, but in a sample of 22
  postings only 1 confirmed remote and 1 confirmed on-site — the other 20 said
  nothing either way, so they are kept on LinkedIn's word alone. Treat
  LinkedIn results as *probably* remote and check the posting before applying.
- The ATS sources (Greenhouse, Ashby) don't have this problem: their location
  fields are authoritative, and Ashby names the country outright. Prefer them
  when you want results you can trust without opening each link.
- LinkedIn caps any single query at roughly 1000 results, which is why the
  default is 8 narrow queries rather than one broad one.
- ATS boards (Greenhouse, Ashby) keep postings live far longer than aggregators,
  so `--days 0` surfaces roles the default 60-day window hides.
- The HN source needs `hacker-news.firebaseio.com`, which some networks block.
