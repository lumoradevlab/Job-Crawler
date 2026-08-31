# Remote Android/Mobile job crawler

Collects **remote-in-the-USA** Android, Kotlin and mobile engineering job links
from 18 job boards in one pass. Python 3 stdlib only — nothing to install, no
virtualenv, no requirements file.

```bash
cd "path/to/Job Crawler"
python3 crawler.py                    # 8 default queries, remote US, last 60 days
python3 test_crawler.py               # 146 tests, no network, under a second
```

The first run takes 10–20 minutes — LinkedIn is paced at 4s a request on
purpose — and every run after it is far quicker, because the date window
narrows to what has appeared since.

Files written each run:

| file | contents |
|---|---|
| `android_remote_jobs_links.txt` | just the URLs, one per line |
| `android_remote_jobs.csv` | open in Excel/Numbers — title, company, location, date, Easy Apply, link |
| `android_remote_jobs.json` | same data for scripting |
| `android_remote_jobs_archive.jsonl` | **every posting ever matched**, appended to and never rewritten |

The first three are rewritten from scratch every run and — unless
`--include-seen` — hold only what was *new*, so run twice in a day and the
second run's `.csv` is nearly empty. The archive is the one that accumulates.

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

## The archive

Reporting only new jobs is a good default and a bad way to store data: the
`.csv` is rewritten each run with just those, and the seen-history keeps a
title, a company and a date — not a location, a salary or a description. So
everything matched is also appended to `<out>_archive.jsonl`, one JSON object
per line, never rewritten.

`--replay` re-filters that file with **no network at all**, which is what to
reach for instead of `--include-seen --full` (that re-crawls everything and
re-spends the Jooble quota to rebuild what is already on disk):

```bash
python3 crawler.py --replay --days 30          # everything matched in the last month
python3 crawler.py --replay --days 0 --strict-us --min-salary 180000
python3 crawler.py --replay --days 0 --source greenhouse ashby -o us_only
```

Every filter still applies — `--days`, `--strict-us`, `--min-salary`,
`--must`/`--exclude` — so the archive doubles as a local database you can
re-query without touching a board. A replay leaves the seen-history alone and
adds nothing to the archive. `--no-archive` turns the appending off.

## Sources

| source | what it is | key? |
|---|---|---|
| `linkedin` | public *guest* search endpoint, no login — by far the highest volume | — |
| `greenhouse` | 44 company ATS boards via the public Greenhouse job-board API | — |
| `ashby` | 35 company ATS boards via the public Ashby posting API | — |
| `lever` | company ATS boards via the public Lever v0 posting API | — |
| `workable` | company ATS boards via Workable's public widget API | — |
| `smartrecruiters` | company ATS boards via the SmartRecruiters posting API | — |
| `himalayas` | himalayas.app remote feed — states its location fence as a country list | — |
| `adzuna` | Adzuna US index — the highest-volume source that carries **salary** | yes |
| `usajobs` | federal postings, remote-flagged and unambiguously US | yes |
| `jooble` | Jooble aggregator, US index | yes |
| `serpapi` | Google Jobs, via SerpApi — the widest net, and the only structured remote flag | yes |
| `builtin` | builtin.com remote engineering board | — |
| `wwr` | We Work Remotely RSS feeds | — |
| `hn` | Hacker News "Ask HN: Who is hiring?" via the official HN API | — |
| `remoteok`, `arbeitnow` | free public APIs — work, but off by default | — |

## The four sources that need a key

All free to start. With no key set, each one prints how to get it and returns
nothing, so the crawler still runs end to end with zero keys configured.

```bash
export ADZUNA_APP_ID="..."      # developer.adzuna.com/signup — instant
export ADZUNA_APP_KEY="..."
export USAJOBS_KEY="..."        # developer.usajobs.gov/apirequest — instant
export USAJOBS_EMAIL="you@example.com"   # must be the address you registered
export JOOBLE_KEY="..."         # jooble.org/api/about — emailed after review
export SERPAPI_KEY="..."        # serpapi.com/users/sign_up — instant
```

Three things these APIs do not tell you up front:

- **USAJOBS authenticates on two headers.** The key goes in `Authorization-Key`
  and *the email you registered with* goes in `User-Agent`. A wrong User-Agent
  is a 401 even when the key is right.
- **`RemoteIndicator` is case-sensitive.** `true` filters to remote postings;
  `True` matches nothing at all and returns an empty result set rather than an
  error, which reads exactly like a quiet week.
- **Jooble's default quota is 500 requests total**, not per day. This crawler
  spends one request per keyword per run, so a default 8-keyword run costs 8.

Adzuna also content-negotiates on `Accept`, and the crawler's default header
offers `text/html` first — so it must ask for `application/json` outright or
the same URL that returns jobs to curl returns Adzuna's HTML docs page.

The ATS sources (Greenhouse, Ashby, Lever, Workable, SmartRecruiters) are the
highest-signal ones: results link straight to the company's own careers page
rather than an aggregator, and their location fields are authoritative.

They have one shared weakness: **there is no company index anywhere**. A slug
is only reachable if you already know the company uses that ATS, so each list
here was built by probing candidates and keeping what answered. Greenhouse,
Ashby, Lever and Workable all 404 an unknown slug; SmartRecruiters answers
`200` with an empty list, so a typo there is silent. And on every one of them
a real company that simply isn't hiring returns exactly what a bad slug
returns, which is why the only usable proof is a board that answers with jobs.

## Google Jobs (`serpapi`)

Google Jobs is the widest net available — it indexes the aggregators and the
company boards alike — but Google publishes no API for it, so this goes
through SerpApi. **It is off by default**; ask for it explicitly:

```bash
python3 crawler.py --source serpapi linkedin greenhouse ashby
```

Two things earn it the key:

- **It is the only source with a structured remote flag.** Every other
  aggregator here — LinkedIn, Adzuna, Jooble — leaves remote to be read out
  of prose. Google states `work_from_home: true` as a boolean, so those
  results don't depend on the weakest part of the crawl.
- **Its links point at the posting, not at Google.** Each result carries a
  `source_link` to whoever published it, which is what gets kept; Google's
  own `share_link` is a search URL and is only a last resort.

**The catch is the quota: SerpApi bills one search per keyword per page, and
the free tier is 250 searches a month.** A default 8-keyword run at one page
spends 8 of them — about thirty runs a month. That is why it is off by
default, why pages are capped at 3 however many you ask for, and why every
run prints what it spent:

```
[serpapi] "Android Developer": 10 matches
  10 postings, 1 SerpApi search spent (free tier is 250 a month)
```

A run of two or three narrow keywords is the way to use it. Quota exhaustion
and a bad key both arrive as an HTTP 200 with an `error` string rather than a
failure, so the source stops on the first one instead of spending the rest.
`https://serpapi.com/account?api_key=...` reports the balance and is itself
free, so checking never costs a search.

Salary comes through where Google states it, in its own phrasing — `84K–96K a
year`, `$40 an hour`, `$5,000 a month` — normalised to yearly dollars like
everything else.

Three things a live run makes obvious, all of them Google's doing rather than
the crawler's:

- **Almost every location comes back as `Anywhere`.** On a sample search that
  was 8 postings out of 8. Those grade as "worldwide", which is kept by
  default and dropped by `--strict-us` — so pairing this source with
  `--strict-us` will return close to nothing, by design.
- **Most postings carry no date.** `posted_at` was absent on 6 of 8, and an
  undated posting always survives the `--days` window, so this source is
  effectively unfiltered by age.
- **The link is whichever board Google indexed**, which on that same sample
  was bebee, lensa and jobmesh as often as Monster, ZipRecruiter or LinkedIn.
  That is why it ranks below LinkedIn when two sources carry one job — the
  other source usually owns the page it points at.

## Growing the board lists

`--discover` closes that gap using what the crawl already knows. Every result
names a company, and a company name is a candidate ATS slug — so the
low-signal aggregators can be made to feed the high-signal ATS boards:

```bash
python3 crawler.py --discover
```

```
[discover] probing 61 candidate slugs across 5 ATSes
  + greenhouse: classpass   (ClassPass)
  + lever: wealthfront      (Wealthfront)
  + workable: intertek      (Intertek)
  9 new boards, 52 slugs did not answer
```

Findings are written to `<out>_boards.json` and merged into the built-in lists
from the next run on, so the crawler grows its own best sources a little each
time. On a first run over ~250 companies from a normal crawl, expect roughly a
1-in-5 hit rate.

What it does to keep the request count sane:

- Company names are normalised into the slugs an ATS might use — `Scribd, Inc.`
  → `scribd`, `Alarm.com` → `alarmcom`, `Epic Games` → `epicgames`,
  `epic-games`, `epic`. Only a *two*-word name gives up its head word, so
  `Bank of America` never probes `bank`.
- ATSes are probed in order and stopped at the first hit, since a company uses
  one. Slugs already in a list — built-in or previously discovered — are never
  probed at all.
- **Misses are remembered for 30 days, then retried.** A company between
  postings is indistinguishable from a typo, so a permanent miss list would
  slowly go wrong.
- 150 candidates per run, the rest picked up next time.

Company fields that clearly aren't company names are skipped rather than
mangled — HN's is the first line of a post, and Built In sometimes carries a
slash-joined pair.

**Not available:** `indeed`, `wellfound` and `dice` block automated access,
`hired` shut down, Remotive's API now returns only 16 jobs in total, and
`arc` and `jobright` both ignore the search keyword — Arc returns the same
~60 generic postings whatever you ask for, so searching it for "Nurse
Practitioner" returns remote Rails jobs. Asking for one of them prints the
reason and returns nothing.

## What each source is actually worth

Measured on one full sweep (`--full --include-seen --days 0`), 417 jobs:

| source | jobs | why |
|---|---|---|
| `linkedin` | 159 | the whole US job market, but its remote filter leaks |
| `serpapi` | 123 | Google Jobs — indexes everything, structured remote flag |
| `builtin` | 120 | a large remote-tech board |
| `hn` | 9 | one thread a month |
| `ashby` + `greenhouse` + `smartrecruiters` | ~25 | **103 named companies** |
| `lever`, `workable`, `himalayas`, `wwr` | 0–1 each | see below |

The ATS boards are the highest-*signal* sources and the lowest-*volume* ones,
and that is structural rather than a bug: Greenhouse scans 6,852 live postings
across 44 companies to find 46 Android/mobile titles, of which 16 are remote.
A given company simply does not have an Android opening most weeks. They are
worth crawling because their locations are authoritative and their links are
the employer's own — not because they will fill a CSV.

**If a run looks thin, the fix is volume, in this order:** set the Adzuna key
(second-highest volume, and it carries salary), use `--source serpapi`, and
run `--discover` to grow the 103 boards. `--why` will tell you which of those
is actually costing you.

```bash
# find new ATS boards from the companies this crawl turns up
python3 crawler.py --discover

# every company ATS board — the highest-signal sources
python3 crawler.py --source greenhouse ashby lever workable smartrecruiters \
    --full --include-seen --days 0

# the sources that state salary, filtered to real money
python3 crawler.py --source adzuna himalayas usajobs --min-salary 150000

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

# why is the result so small? name the rule that dropped each posting
python3 crawler.py --why

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
| `--lever-boards` | override the Lever company list |
| `--workable-boards` | override the Workable company list |
| `--sr-boards` | override the SmartRecruiters company list |
| `--discover` | probe crawled company names for new ATS boards, and remember them |
| `--min-salary` | drop postings whose stated pay is below N (keeps unstated) |
| `--details` | fetch each LinkedIn posting: description + Easy Apply flag |
| `--easy-apply-only` | keep only Easy Apply jobs (requires `--details`) |
| `--must` / `--exclude` | keyword filters |
| `--no-filter` | keep every hit, skip the Android/mobile title gate |
| `--why` | explain every rejection, and write `<out>_rejected.csv` |
| `--strict-us` | require the posting to name the US (drops worldwide/unlabelled) |
| `--anywhere` | switch the US gate off entirely |
| `--delay` | seconds between LinkedIn requests, default 4 — **don't lower this** |
| `-o, --out` | output basename, default `android_remote_jobs` |
| `--replay` | rebuild the outputs from the archive, no network |
| `--no-archive` | don't append this run's matches to the archive |
| `--include-seen` | report every match, not just new ones |
| `--reset-seen` | forget the run history and start again |
| `--state` | history file, default `<out>_seen.json` |

## Tests

```bash
python3 test_crawler.py            # all of it, no network
python3 test_crawler.py -v         # naming each case
python3 test_crawler.py TestKeep   # one class
```

81 cases over the grading logic — `us_status()`, `keep()`, `parse_salary()`,
`relative_date()` and the regexes behind them. All of it is pure functions, so
the suite never touches the network and runs in well under a second.

This is where a regex tune proves it didn't break a case that used to work,
which is worth more here than in most projects: nearly every decision the
crawler makes is a regex, and most of them were arrived at by tuning against
whatever a board happened to return that week.

Two rules the suite exists to defend, both of which look wrong until you
check them against real results:

- **A location may forgive an on-site title.** Judging the title alone seems
  stricter and is actually a 19% loss — Built In prepends its workplace tag
  to the place, so `In-Office or Remote Dallas, TX` *is* the board saying
  remote is on the table. LinkedIn locations never say "remote", so the title
  is already the only signal there and nothing is lost by leaving this loose.
- **A split week is hybrid however cheerfully worded.** `4 days remote` reads
  as generous and still means an office one day a week.

## How it grades a posting

Every source is normalised into one record and put through the same gate: an
Android/mobile title, a genuine remote flag, and a US check.

The US check is the fiddly part, because boards state it in free text. A
posting fenced to another region is dropped; "Worldwide"/"Anywhere" is kept (a
US applicant qualifies); `--strict-us` keeps only postings that name the US.
Ashby, Workable, SmartRecruiters and Himalayas state the country outright —
everywhere else it is read out of prose, so the location column is worth a
glance.

**Two sources have no remote field at all.** Adzuna and Jooble cannot be asked
for remote work; the word can only go in the query, and what comes back is the
company's own city. Their postings are graded from the words alone, the same
way LinkedIn's are, so treat them the way you treat LinkedIn.

Google Jobs is the opposite case and the best of the aggregators for this:
`work_from_home` is a boolean the API states outright, so those results do not
depend on reading the workplace out of prose at all.

**Salary** is collected where a source states it: Adzuna, Himalayas, USAJOBS,
Jooble and Google Jobs. Adzuna flags whether a figure is the posting's own number or its
model's estimate — estimates are dropped rather than reported as fact — and
Jooble states pay as prose (`$100k - $120k`, `$80 per hour`), which is parsed
and annualised at 2080h so `--min-salary` can judge both.

**When two sources carry the same job**, the record kept is the one with the
best link, not the one crawled first: a company's own ATS outranks an
aggregator, whose redirect expires and whose location string is a guess. Any
salary the losing record knew is carried across.

Two sources rarely spell one job identically, so they are compared with
punctuation, a trailing workplace or location, and a company's legal suffix
normalised away — `Mobile Engineer II (Android)` matches `Mobile Engineer II,
Android`, and `Reddit, Inc.` matches `Reddit`. Seniority, a team in brackets
and employment type are deliberately *not* normalised: `Senior Android
Engineer` is not `Android Engineer`, `(Payments)` is a different job, and a
contract post is a different opening. Dedupe that merges too much drops
postings silently, which is worse than listing one twice.

A bare "remote" in a job body is ignored deliberately: boilerplate like "if the
role can be performed remotely" appears in strictly on-site postings, so only
committed phrasings ("fully remote", "US-Remote") count when the location field
itself is silent.

## Why isn't it in the results?

The gate is silent by default, and silence is ambiguous — a run returning 12
jobs out of 900 looks the same whether it was a quiet week or a regex that
stopped matching. `--why` answers it:

```bash
python3 crawler.py --source greenhouse ashby --why
```

```
[why] 1083 postings rejected (1078 of them by a source's own title gate)
  not-mobile     1078   greenhouse 781, ashby 297
  not-remote        5   greenhouse 5
  -> android_remote_jobs_rejected.csv
```

The CSV names the rule against each dropped posting, so a company that never
appears can be grepped for directly:

```bash
grep -i stripe android_remote_jobs_rejected.csv
```

The rules, in the order they fire — the first one to match is the one
reported, so a posting breaking three of them names only the first:

| reason | what it means |
|---|---|
| `not-mobile` | no Android/mobile word *and* role word in the title |
| `must` / `exclude` | your own `--must` / `--exclude` filters |
| `easy-apply` | `--easy-apply-only`, and this isn't one |
| `not-remote` | the source never flagged it remote |
| `hybrid-split` | a split week — "3 days onsite", "2x week in office" |
| `onsite` | names an office and never says remote |
| `region` | fenced outside the US |
| `not-us` | `--strict-us`, and the location doesn't name the US |
| `too-old` | outside the `--days` window |
| `duplicate` | another source carried the same job on a better link |

Two things worth knowing about the counts. `not-mobile` dominates every run
and is mostly uninteresting — it's every non-mobile role on the boards being
crawled, which on Greenhouse is ~99% of them. And most sources apply that
title gate themselves before paying for a detail fetch, so those drops are
counted here separately rather than being invisible.

The reason to reach for this is the **`not-remote` and `onsite` rows**. That
is the crawl's weakest call, and reading a few dozen of them is the cheap
version of the hand-sampling described below.

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
