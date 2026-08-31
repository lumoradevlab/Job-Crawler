#!/usr/bin/env python3
"""Crawl remote Android/mobile developer jobs, US-only by default.

Every source is normalised into the same record and graded through the same
gates: an Android/mobile title, a genuine remote flag, and us_status() — which
drops postings fenced to another region, keeps "Worldwide"/"Anywhere" ones (a
US applicant qualifies), and under --strict-us keeps only those naming the US.

Working sources
  linkedin    public "guest" job search endpoint (no login)
  greenhouse  company ATS boards, via the public Greenhouse job-board API
  ashby       company ATS boards, via the public Ashby posting API
  builtin     builtin.com remote board (server-rendered job cards)
  arc         arc.dev remote board (Next.js payload)
  wwr         We Work Remotely RSS feeds
  hn          Hacker News "Ask HN: Who is hiring?" via the official HN API
  remotive / remoteok / arbeitnow
              free public job APIs, no key needed

Unavailable sources, kept so they explain themselves if you ask for them:
  indeed, wellfound, dice, hired, jobright -- see BLOCKED.

Python 3 stdlib only. Run `python3 crawler.py --help` for examples.
"""

import argparse
import concurrent.futures as futures
import csv
import gzip
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

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


# ==========================================================================
# HTTP
# ==========================================================================
def fetch(url, tries=4, timeout=20, headers=None):
    """GET a URL, returning decoded text. Backs off on 429/5xx."""
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    }
    if headers:
        h.update(headers)
    delay = 5.0
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if e.code in (429, 500, 502, 503) and attempt < tries:
                wait = delay * attempt + random.uniform(0, 2)
                print(f"  ! HTTP {e.code}, backing off {wait:.0f}s "
                      f"(try {attempt}/{tries})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  ! HTTP {e.code} on {url}", file=sys.stderr)
            return ""
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries:
                time.sleep(delay * attempt)
                continue
            print(f"  ! {e} on {url}", file=sys.stderr)
            return ""
    return ""


def fetch_json(url, **kw):
    text = fetch(url, **kw)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def strip_tags(html):
    html = re.sub(r"<br\s*/?>", "\n", html)
    html = re.sub(r"</(p|li|ul|div)>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    # unescape twice: HN and some RSS feeds double-encode their entities
    html = unescape(unescape(html))
    html = re.sub(r"[ \t]+", " ", html)
    return re.sub(r"\n{3,}", "\n\n", html).strip()


def next_data(html):
    """Pull the pageProps object out of a Next.js page."""
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    except json.JSONDecodeError:
        return {}


# Aggregators state pay as prose — "$100k - $120k", "$80 per hour" — where
# the ATS boards give numbers. Hourly rates are annualised at 2080h so one
# --min-salary threshold can judge both.
SALARY_TEXT = re.compile(
    r"\$\s*([\d,.]+)\s*(k)?\s*(?:-|to|–)?\s*(?:\$\s*([\d,.]+)\s*(k)?)?"
    r"\s*(per\s+hour|/\s*hr|an\s+hour|hourly)?", re.I)


def parse_salary(text):
    """Return (min, max) in yearly dollars, or (None, None)."""
    if not text:
        return None, None
    m = SALARY_TEXT.search(text)
    if not m:
        return None, None

    def num(raw, k):
        if not raw:
            return None
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            return None
        if k:
            v *= 1000
        return v

    lo, hi = num(m.group(1), m.group(2)), num(m.group(3), m.group(4))
    if lo and m.group(5):                 # an hourly rate, annualised
        lo, hi = lo * 2080, (hi * 2080 if hi else None)
    # A bare "$120" is a rate fragment, not a salary; treat it as unstated.
    if lo and lo < 1000:
        return None, None
    return lo, (hi or None)


# Boards that state a rate say which interval it is in. An hourly figure
# left raw reads as an $80 salary and is dropped by any realistic
# --min-salary, so everything is normalised to yearly dollars at 2080h.
HOURLY_RATE = re.compile(r"^(ph|per\s*hour|hourly|hour)$", re.I)


def annualise(amount, interval):
    """Normalise a stated rate to yearly dollars, or None if there isn't one."""
    if amount is None or amount == "":
        return None
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    return value * 2080 if HOURLY_RATE.match((interval or "").strip()) else value


# ==========================================================================
# The shared record
# ==========================================================================
def row(source, title, company, location, url, posted="", remote=True,
        us=None, description="", match_text=None, **extra):
    """Every source returns these keys, so one gate can judge them all."""
    d = {
        "source": source,
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "url": url or "",
        "posted": posted or "",
        "remote": remote,
        "us": us,
        "description": (description or "")[:2000],
        "match_text": match_text,
        "easy_apply": "?",
        "apply_url": "",
        "query": "",
        # Only a few sources state pay; the rest leave these empty.
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_predicted": "",
    }
    d.update(extra)
    return d


# ==========================================================================
# Relevance, remote and US grading
# ==========================================================================
RELEVANT = re.compile(
    r"\b(android|kotlin|jetpack\s*compose|mobile|react\s*native|flutter|"
    r"ios\s*/?\s*android)\b", re.I
)
ROLE = re.compile(
    r"\b(developer|engineer|engineering|programmer|swe|architect|"
    r"development)\b", re.I
)

# Most sources apply the title gate themselves, before paying for a detail
# fetch — so a non-mobile title never reaches keep() and --why would report
# nothing about the rule that rejects the most postings by far. Routing all
# of them through one helper keeps that visible. list.append is atomic under
# the GIL, so the crawlers' worker threads can write here without a lock.
_SKIPPED = []


def relevant(title, args):
    """The Android/mobile title gate the sources apply for themselves."""
    if args.no_filter or (RELEVANT.search(title) and ROLE.search(title)):
        return True
    if getattr(args, "why", False):
        _SKIPPED.append((getattr(args, "source_now", "?"), title))
    return False

REMOTE_HINT = re.compile(r"\b(remote|distributed|work\s+from\s+home|wfh)\b", re.I)
# A bare "remote" in a job body means nothing — boilerplate like "if the role
# can be performed remote" appears in postings that are strictly on-site. Only
# these committed phrasings count when the location field itself is silent.
REMOTE_STRONG = re.compile(
    r"(fully\s+remote|100%\s+remote|remote[-\s]first|work\s+from\s+anywhere|"
    r"remote\s*\(\s*us|remote\s*[-,]\s*(us|united\s+states|anywhere)|"
    r"\bus\s*[-,]?\s*remote|this\s+(role|position)\s+is\s+(fully\s+)?remote|"
    r"\bremote\s+(position|role|opportunity|employee)|open\s+to\s+remote|"
    r"remote[-\s]friendly|distributed\s+team)", re.I
)

# LinkedIn's f_WT=2 "Remote" filter leaks — roles titled "(Hybrid)" or
# "- Onsite" come back inside it — and its guest pages carry no workplace-type
# field to check, only Seniority, Employment type, Job function and Industries.
# So the workplace has to be read out of the words instead.
#
# "hybrid" is the trap: in mobile it is also a stack ("hybrid app developer"
# means React Native, not a hybrid office), so it only counts as a workplace
# when it isn't describing the technology.
ONSITE = re.compile(
    r"\b(on-?site|in-office|in\s+the\s+office|"
    r"hybrid(?!\s*(app|application|mobile|cloud|framework|native|stack)))\b",
    re.I,
)
# The mirror of REMOTE_STRONG. A bare "onsite" in a body means nothing either
# — "onsite interviews", "onsite with customers" — so only these committed
# phrasings are allowed to overrule a board's own remote flag.
# A split week is a hybrid job however cheerfully it is worded. These titles
# name both workplaces — "3 days onsite 2 days remote" — so the "onsite is
# forgivable when remote is also offered" rule below reads them as remote and
# keeps them. Aggregator titles are full of this, so it is matched outright.
HYBRID_SPLIT = re.compile(
    r"\b\d\s*(days?|x)\s*(a|per)?\s*week?s?\s*"
    r"(in\s+(the\s+)?office|on-?site|onsite|in-?office|remote)\b"
    r"|\b\d\s*days?\s*(on-?site|onsite|in\s+office|remote)\b"
    r"|\bhybrid\s*[-–:(]\s*\d\s*days?\b", re.I)

ONSITE_STRONG = re.compile(
    r"((this\s+)?(role|position)\s+is\s+(fully\s+)?(on-?site|hybrid)|"
    r"\b(on-?site|hybrid)\s+(role|position|schedule)\b|"
    r"location\s*[-–:]\s*[^.\n]{0,40}\b(hybrid|on-?site)\b|"
    r"require[ds]?\s+to\s+work\s+(on-?site|in\s+the\s+office)|"
    r"\d\s*days?\s+(a|per)\s+week\s+in\s+(the\s+)?office)", re.I
)


# A remote posting is only useful here if a US-based applicant may hold it.
# Boards state that in free text ("Remote - US", "Anywhere", "EMEA only"), so
# every source is graded through us_status() rather than trusted.
STATES = """alabama alaska arizona arkansas california colorado connecticut
delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky
louisiana maine maryland massachusetts michigan minnesota mississippi missouri
montana nebraska nevada hampshire jersey mexico york carolina dakota ohio
oklahoma oregon pennsylvania rhode tennessee texas utah vermont virginia
washington wisconsin wyoming""".split()

ABBREV = set("""AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA
MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA
WV WI WY DC""".split())

# "Remote (US)" and "US-Remote" are two of the commonest ways an ATS states
# a US-remote role, and neither reads as a plain "Remote - US" — so the
# bracket, the em dash and the reversed order all have to be spelled out.
# "us" on its own is deliberately absent: this pattern is also run over job
# bodies, where "come work with us" would otherwise grade as a US posting.
US_HINT = re.compile(
    r"\b(united\s+states|u\.s\.?a?|usa|us[\s-]+only|us[\s-]+based|"
    r"anywhere\s+in\s+the\s+us|remote\s*[-–—,(\[]*\s*us|"
    r"us\s*[-–—]\s*remote|nationwide)\b", re.I
)
WORLDWIDE = re.compile(
    r"\b(anywhere|worldwide|global|remote\s*[-,]?\s*global|"
    r"no\s+preference|any\s+location)\b", re.I
)
NON_US = re.compile(
    r"\b(emea|apac|latam|europe|european|uk\b|united\s+kingdom|ireland|"
    # "New England" is in Massachusetts, so England only counts unprefixed.
    r"(?<!new\s)england|scotland|wales|britain|"
    r"germany|france|spain|portugal|poland|netherlands|india|pakistan|"
    r"philippines|singapore|australia|canada|brazil|argentina|mexico\s+city|"
    r"nigeria|kenya|japan|china|korea|vietnam|indonesia|turkey|romania|"
    r"ukraine|serbia|bulgaria|czech|hungary|greece|israel|uae|dubai)\b", re.I
)
# "Remote (CET ±3)" style timezone fences that rule out a US-based applicant.
NON_US_TZ = re.compile(r"\b(cet|cest|eet|eest|bst|ist|gmt\s*[+±])\b", re.I)


def us_status(text):
    """Grade a location string: 'us', 'worldwide', 'no', or 'unknown'.

    Order matters here. An explicit US signal — the country named, or a
    ", CA" style abbreviation — is checked first and wins outright. Only
    then is the posting tested for another region, and the substring pass
    over STATES runs last of the three: it is much the loosest test, and
    going first it claims "Yorkshire" for New York, "Hampshire" for New
    Hampshire and "Mexico City" for New Mexico.
    """
    if not text:
        return "unknown"
    t = text.strip()
    if NON_US_TZ.search(t) and not US_HINT.search(t):
        return "no"
    if US_HINT.search(t):
        return "us"
    if set(re.findall(r",\s*([A-Z]{2})\b", t)) & ABBREV:
        return "us"
    if NON_US.search(t):
        return "no"
    if any(s in t.lower() for s in STATES):
        return "us"
    if WORLDWIDE.search(t):
        return "worldwide"
    return "unknown"


def keep(job, args):
    """The single gate every posting must pass, whatever its source."""
    return rejection(job, args) is None


def rejection(job, args):
    """Which rule rejects this posting, or None if it passes them all.

    keep() is this function's yes/no shadow, so --why can name the rule that
    actually fired instead of a second copy of the rules drifting alongside
    the real ones. Every reason reads "<category>: <detail>"; the category is
    what the summary groups on, so keep those stable and the detail specific.
    """
    title = job["title"]
    # Structured boards put the role in the title; free-text sources (HN)
    # bury it in the body, so they set match_text to widen the gate.
    subject = job.get("match_text") or title

    if not args.no_filter and not (RELEVANT.search(subject)
                                   and ROLE.search(subject)):
        return "not-mobile: no Android/mobile role in the title"
    if args.must:
        hay = (title + " " + job.get("description", "")).lower()
        absent = [w for w in args.must if w.lower() not in hay]
        if absent:
            return "must: never says " + ", ".join(absent)
    if args.exclude:
        banned = [w for w in args.exclude if w.lower() in title.lower()]
        if banned:
            return "exclude: title says " + ", ".join(banned)
    if args.easy_apply_only and job.get("easy_apply") != "yes":
        return "easy-apply: not an Easy Apply posting"
    if not job.get("remote"):
        return "not-remote: the source never flagged it remote"
    # A title that names the workplace outranks whatever the board's own
    # remote filter claimed — unless it offers both ("Remote or Hybrid").
    where = title + " " + job.get("location", "")
    split = HYBRID_SPLIT.search(where)
    if split:
        return "hybrid-split: %r is a week split with an office" % (
            split.group(0).strip(),)
    # Read the workplace across title and location together. Tightening this
    # to the title alone looks right and is not: LinkedIn locations never say
    # "remote" (so the title is already the only signal there), while Built In
    # prepends its workplace tag to the place — "In-Office or Remote Dallas,
    # TX" — and that tag is the board stating the role genuinely offers both.
    office = ONSITE.search(where)
    if office and not REMOTE_HINT.search(where):
        return "onsite: says %r and never says remote" % (
            office.group(0).strip(),)

    if not args.anywhere:
        status = job.get("us") or us_status(job.get("location", ""))
        if status == "no":
            return "region: fenced outside the US (%s)" % (
                job.get("location") or "no location given",)
        if args.strict_us and status != "us":
            return "not-us: --strict-us, and the location reads %s" % status

    if args.days and job.get("posted"):
        try:
            posted = datetime.strptime(job["posted"][:10], "%Y-%m-%d")
            if posted < datetime.now() - timedelta(days=args.days):
                return "too-old: posted %s, window is %d days" % (
                    job["posted"][:10], args.days)
        except ValueError:
            pass
    return None


# ==========================================================================
# Source: LinkedIn (public guest endpoint)
# ==========================================================================
LINKEDIN_SEARCH = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)
LINKEDIN_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

WORKPLACE_REMOTE = "2"          # f_WT: 1=on-site 2=remote 3=hybrid
EXPERIENCE = {                  # f_E
    "internship": "1", "entry": "2", "associate": "3",
    "mid": "4", "senior": "4", "director": "5", "executive": "6",
}
# LinkedIn resolves a location string server-side, but only a geoId is exact;
# "United States" alone sometimes drifts into worldwide results.
GEO_IDS = {
    "united states": "103644278",
    "usa": "103644278",
    "us": "103644278",
    "worldwide": "92000000",
}


class JobCardParser(HTMLParser):
    """Pulls job cards out of the guest search HTML fragment."""

    FIELDS = {
        "base-search-card__title": "title",
        "base-search-card__subtitle": "company",
        "job-search-card__location": "location",
    }

    def __init__(self):
        super().__init__()
        self.jobs = []
        self._cur = None
        self._capture = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = a.get("class", "")

        # A new card starts at the element carrying the job urn.
        urn = a.get("data-entity-urn", "")
        if "jobPosting:" in urn:
            self._flush()
            self._cur = {"job_id": urn.rsplit(":", 1)[-1]}

        if self._cur is None:
            return

        if tag == "a" and "href" in a and not self._cur.get("url"):
            if "/jobs/view/" in a["href"]:
                self._cur["url"] = a["href"].split("?")[0]

        if tag == "time" and "datetime" in a:
            self._cur["posted"] = a["datetime"]

        for css, field in self.FIELDS.items():
            if css in classes and field not in self._cur:
                self._capture, self._buf = field, []
                break

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._capture:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if text:
                self._cur[self._capture] = text
                self._capture = None

    def close(self):
        super().close()
        self._flush()

    def _flush(self):
        if self._cur and self._cur.get("title"):
            for k in ("company", "location", "posted"):
                self._cur.setdefault(k, "")
            self._cur.setdefault(
                "url",
                "https://www.linkedin.com/jobs/view/" + self._cur["job_id"],
            )
            self.jobs.append(self._cur)
        self._cur = None


def linkedin_detail(job_id):
    """Description, Easy Apply flag and external apply URL for one posting.

    LinkedIn marks the apply button 'apply-link-onsite' for Easy Apply and
    'apply-link-offsite' when it hands you off to the company's own site.
    """
    html = fetch(LINKEDIN_DETAIL.format(job_id))
    if not html:
        return {}

    out = {}
    m = re.search(
        r'class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
        html, re.S,
    )
    out["description"] = strip_tags(m.group(1))[:2000] if m else ""

    if "apply-link-onsite" in html:
        out["easy_apply"] = "yes"
    elif "apply-link-offsite" in html:
        out["easy_apply"] = "no"
    else:
        out["easy_apply"] = "?"

    m = re.search(r'<code id="applyUrl"[^>]*>\s*<!--"?(.*?)"?-->', html, re.S)
    if m:
        out["apply_url"] = urllib.parse.unquote(m.group(1).strip()).strip('"')

    # The body is the only other place the workplace is stated, and it is the
    # one that catches postings whose title stays silent about it.
    body = out["description"]
    if ONSITE_STRONG.search(body) and not REMOTE_STRONG.search(body):
        out["remote"] = False
    return out


def crawl_linkedin(args):
    """Run every keyword query, paging until a query runs dry."""
    seen, jobs = set(), []

    for query in args.keywords:
        params = {
            "keywords": query,
            "location": args.location,
            "f_WT": WORKPLACE_REMOTE,
            "sortBy": "DD",  # most recent first
        }
        geo = GEO_IDS.get(args.location.strip().lower())
        if geo:
            params["geoId"] = geo
        if args.days:
            params["f_TPR"] = "r%d" % (args.days * 86400)
        if args.level:
            params["f_E"] = EXPERIENCE[args.level]

        print(f'[linkedin] query: "{query}"')
        for page in range(args.pages):
            params["start"] = page * 10
            html = fetch(LINKEDIN_SEARCH + "?" + urllib.parse.urlencode(params))
            if not html.strip():
                print(f"  page {page + 1}: empty — end of this query")
                break

            parser = JobCardParser()
            parser.feed(html)
            parser.close()
            if not parser.jobs:
                print(f"  page {page + 1}: 0 cards — end of this query")
                break

            new = 0
            for card in parser.jobs:
                if card["job_id"] in seen:
                    continue
                seen.add(card["job_id"])
                jobs.append(row(
                    "linkedin", card["title"], card["company"],
                    card["location"], card["url"], card["posted"],
                    remote=True, query=query, job_id=card["job_id"],
                ))
                new += 1
            print(f"  page {page + 1}: {len(parser.jobs)} cards, "
                  f"+{new} new (running total {len(jobs)})")
            time.sleep(args.delay + random.uniform(0, 1.5))

    if args.details:
        # One request per posting, so only pay it for jobs we haven't read.
        known = getattr(args, "seen_keys", set())
        todo = [j for j in jobs if job_key(j) not in known]
        print(f"[linkedin] fetching details for {len(todo)} new jobs "
              f"(~{len(todo) * args.delay / 60:.0f} min)")
        for i, job in enumerate(todo, 1):
            job.update(linkedin_detail(job["job_id"]))
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}")
            time.sleep(args.delay + random.uniform(0, 1.5))
    return jobs


# ==========================================================================
# Source: Greenhouse company boards
# ==========================================================================
# Verified live: every token below returns jobs from the public board API.
GREENHOUSE_BOARDS = """
databricks stripe anthropic waymo lucidmotors brex braze roblox pinterest
samsara scaleai affirm airbnb lyft coinbase figma epicgames klaviyo reddit
asana robinhood instacart gusto duolingo faire twitch mercury sofi carta
chime mixpanel peloton discord attentive dropbox betterment amplitude
life360 webflow coursera squarespace udemy masterclass medium
""".split()

GH_LIST = "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=false"
GH_JOB = "https://boards-api.greenhouse.io/v1/boards/{}/jobs/{}"


def crawl_greenhouse(args):
    """Two passes: cheap title listing, then full text for the hits only.

    A location of "San Francisco, CA" doesn't mean the role isn't
    remote-eligible — Greenhouse keeps that detail in the body, so only the
    handful of Android/mobile matches pay for a second request.
    """
    boards = args.boards or board_list("greenhouse", GREENHOUSE_BOARDS, args)
    print(f"[greenhouse] listing {len(boards)} company boards")
    listed = []

    def board(token):
        data = fetch_json(GH_LIST.format(token), tries=2) or {}
        out = []
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            out.append(row(
                "greenhouse", j.get("title", ""),
                j.get("company_name") or token.title(), loc,
                j.get("absolute_url", ""),
                (j.get("updated_at") or j.get("first_published") or "")[:10],
                remote=bool(REMOTE_HINT.search(loc)),
                gh_token=token, gh_id=j.get("id"),
            ))
        return out

    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)

    hits = [j for j in listed if relevant(j["title"], args)]
    print(f"  {len(listed)} postings scanned, {len(hits)} Android/mobile titles")

    # The board listing is one request per company, but each full posting is
    # its own request — so never re-read one already in the history.
    known = getattr(args, "seen_keys", set())
    hits = [j for j in hits if job_key(j) not in known]
    if known:
        print(f"  {len(hits)} of those are new; skipping the rest")

    def detail(j):
        data = fetch_json(GH_JOB.format(j["gh_token"], j["gh_id"]), tries=2)
        if not data:
            return j
        body = strip_tags(data.get("content", ""))
        j["description"] = body[:2000]
        # The location field is the reliable signal; the body only counts if
        # it commits to remote in so many words.
        j["remote"] = bool(REMOTE_HINT.search(j["location"])) or \
            bool(REMOTE_STRONG.search(body))
        located = us_status(j["location"])
        j["us"] = located if located != "unknown" else us_status(body[:1500])
        return j

    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        hits = list(ex.map(detail, hits))
    print(f"  {sum(1 for j in hits if j['remote'])} of them are remote")
    return hits


# ==========================================================================
# Source: Ashby company boards
# ==========================================================================
# Verified live: every token below answers the public posting API with jobs.
ASHBY_BOARDS = """
openai notion plaid ramp vanta linear strava sentry supabase cursor harvey
elevenlabs abridge headway sierra decagon watershed render posthog resend
substack patreon incident railway warp modal hex temporal applied mercor
browserbase openevidence neon unit mux
""".split()

ASHBY_LIST = "https://api.ashbyhq.com/posting-api/job-board/{}"

# Ashby names the country outright, so US-ness never has to be guessed from
# prose the way it does everywhere else.
US_COUNTRY = {"united states", "usa", "us", "united states of america"}


def ashby_places(job):
    """Every location on a posting: the primary one plus its alternates.

    Returns (label, countries). A posting headquartered in New York but open
    to "Remote (US)" carries that only in secondaryLocations, so reading the
    primary location alone would misjudge both the remote flag and the country.
    """
    places, countries = [], set()

    def take(loc, addr):
        if loc:
            places.append(loc)
        country = ((addr or {}).get("postalAddress") or {}).get("addressCountry")
        if country:
            countries.add(country.strip().lower())

    take(job.get("location"), job.get("address"))
    for sec in job.get("secondaryLocations") or []:
        take(sec.get("location"), sec.get("address"))
    return " / ".join(dict.fromkeys(places)), countries


def crawl_ashby(args):
    """One request per company — the posting API returns the full postings.

    Unlike Greenhouse there is no second fetch per job: each record already
    carries its description, an isRemote flag and a per-country location
    list, which is the cleanest US signal any source here offers.
    """
    boards = args.ashby_boards or board_list("ashby", ASHBY_BOARDS, args)
    print(f"[ashby] listing {len(boards)} company boards")

    def board(token):
        data = fetch_json(ASHBY_LIST.format(token), tries=2) or {}
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            title = j.get("title", "").strip()
            if not relevant(title, args):
                continue
            label, countries = ashby_places(j)
            if countries:
                status = "us" if countries & US_COUNTRY else "no"
            else:
                status = us_status(label)
            body = strip_tags(j.get("descriptionHtml", ""))
            out.append(row(
                "ashby", title, token.title(), label or "Unspecified",
                j.get("jobUrl", ""), (j.get("publishedAt") or "")[:10],
                # workplaceType can read "Hybrid" while a "Remote (US)"
                # alternate location exists, so isRemote is the flag to trust.
                remote=bool(j.get("isRemote"))
                or bool(REMOTE_HINT.search(label)),
                us=status, description=body,
                apply_url=j.get("applyUrl", ""),
            ))
        return out

    listed = []
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)
    print(f"  {len(listed)} Android/mobile titles, "
          f"{sum(1 for j in listed if j['remote'])} of them remote")
    return listed


# ==========================================================================
# Source: Built In
# ==========================================================================
BUILTIN_URL = "https://builtin.com/jobs/remote/dev-engineering"

# One card per posting. The fields hang off data-id attributes and off the
# icon that precedes each value, which is what these patterns anchor to.
BI_CARD = 'data-id="job-card"'
BI_TITLE = re.compile(r'data-id="job-card-title"[^>]*>(.*?)</a>', re.S)
BI_ALIAS = re.compile(r'data-alias="([^"]+)"')
BI_COMPANY = re.compile(r'data-id="company-title"[^>]*>\s*<span>(.*?)</span>', re.S)
# The value sometimes sits in a bare <div> wrapper and sometimes doesn't,
# so both patterns tolerate it — without it the country is silently lost and
# a Berlin posting reads as an unlabelled "Remote".
BI_VALUE = r'[^>]*></i>\s*</div>\s*(?:<div>\s*)?<span[^>]*>([^<]*)</span>'
BI_WORKPLACE = re.compile("fa-house-building" + BI_VALUE)
BI_LOCATION = re.compile("fa-location-dot" + BI_VALUE)
# Cards open to several cities list them in a tooltip instead of the span.
BI_LOCATIONS = re.compile(r'aria-label="Job locations" data-bs-title="(.*?)">', re.S)
BI_POSTED = re.compile(r'fa-clock[^>]*></i>([^<]*)</span>')
BI_BLURB = re.compile(
    r'<div class="fs-sm fw-regular mb-md text-gray-04">(.*?)</div>', re.S)


# Built In writes countries as ISO-3 codes — "Berlin, DEU", "Amsterdam, NLD" —
# which us_status() cannot read, since it looks for prose country names. Left
# to it, every European posting here grades "unknown" and survives the US gate.
BI_ISO3 = re.compile(r"\b([A-Z]{3})\b")


def builtin_us(location):
    """Grade a Built In location, reading its ISO-3 country codes first."""
    codes = set(BI_ISO3.findall(location))
    if codes:
        # A posting open to several countries still qualifies if one is ours.
        return "us" if "USA" in codes else "no"
    return us_status(location)


def builtin_date(text, today=None):
    """Turn "Reposted 3 Days Ago" into a date; Built In posts no timestamps."""
    today = today or datetime.now()
    t = text.replace("Reposted", "").strip().lower()
    if not t:
        return ""
    if t.startswith(("today", "just", "moments")) or "hour" in t or "minute" in t:
        return today.strftime("%Y-%m-%d")
    if t.startswith("yesterday"):
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.match(r"(\d+)\+?\s+days?\s+ago", t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    return ""


def crawl_builtin(args):
    """Search the remote engineering board, one keyword at a time.

    The /jobs/remote/ path is not a remote guarantee — cards inside it still
    come back tagged "Hybrid" or "In-Office" — so the workplace tag on each
    card is what decides.
    """
    jobs, seen = [], set()
    for query in args.keywords:
        found = 0
        for page in range(1, args.pages + 1):
            url = BUILTIN_URL + "?" + urllib.parse.urlencode(
                {"search": query, "page": page})
            cards = fetch(url).split(BI_CARD)[1:]
            if not cards:
                break
            for raw in cards:
                card = re.sub(r"\s+", " ", raw)
                alias = BI_ALIAS.search(card)
                title = BI_TITLE.search(card)
                if not (alias and title):
                    continue
                link = "https://builtin.com" + alias.group(1)
                if link in seen:
                    continue
                seen.add(link)

                multi = BI_LOCATIONS.search(card)
                if multi:
                    location = ", ".join(
                        p for p in strip_tags(unescape(multi.group(1))).split("\n")
                        if p.strip())
                else:
                    m = BI_LOCATION.search(card)
                    location = strip_tags(m.group(1)) if m else ""
                workplace = BI_WORKPLACE.search(card)
                workplace = strip_tags(workplace.group(1)) if workplace else ""
                company = BI_COMPANY.search(card)
                posted = BI_POSTED.search(card)
                blurb = BI_BLURB.search(card)

                jobs.append(row(
                    "builtin", strip_tags(title.group(1)),
                    strip_tags(company.group(1)) if company else "",
                    (workplace + " " + location).strip(), link,
                    builtin_date(strip_tags(posted.group(1)) if posted else ""),
                    remote=bool(REMOTE_HINT.search(workplace)),
                    us=builtin_us(location),
                    description=strip_tags(blurb.group(1)) if blurb else "",
                    query=query,
                ))
                found += 1
            time.sleep(1)
        print(f'[builtin] "{query}": {found} postings')
    print(f"  {sum(1 for j in jobs if j['remote'])} of {len(jobs)} are remote")
    return jobs


# ==========================================================================
# Source: Arc.dev
# ==========================================================================
def crawl_arc(args):
    jobs = []
    for q in args.keywords:
        url = "https://arc.dev/remote-jobs?" + urllib.parse.urlencode({"search": q})
        props = next_data(fetch(url))
        found = 0
        for key in ("arcJobs", "externalJobs"):
            for j in props.get(key) or []:
                countries = j.get("requiredCountries") or []
                if countries:
                    status = "us" if "US" in countries else "no"
                else:
                    status = "worldwide"
                posted = ""
                if isinstance(j.get("postedAt"), (int, float)):
                    posted = datetime.fromtimestamp(
                        j["postedAt"], timezone.utc).strftime("%Y-%m-%d")
                jobs.append(row(
                    "arc", j.get("title", ""),
                    (j.get("company") or {}).get("name", ""),
                    ", ".join(countries) or "Worldwide",
                    "https://arc.dev/remote-jobs/" + (j.get("urlString") or ""),
                    posted, remote=True, us=status, query=q,
                ))
                found += 1
        print(f'[arc] "{q}": {found} postings')
        time.sleep(1)
    return jobs


# ==========================================================================
# Source: We Work Remotely (RSS)
# ==========================================================================
WWR_FEEDS = [
    "remote-programming-jobs",
    "remote-full-stack-programming-jobs",
    "remote-back-end-programming-jobs",
    "remote-front-end-programming-jobs",
]


def crawl_wwr(args):
    jobs = []
    for feed in WWR_FEEDS:
        xml = fetch(f"https://weworkremotely.com/categories/{feed}.rss")
        items = re.findall(r"<item>(.*?)</item>", xml, re.S)
        for it in items:
            def tag(name, block=it):
                m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
                return strip_tags(m.group(1)).strip() if m else ""

            raw_title = tag("title")
            # WWR titles read "Company: Role"
            company, _, title = raw_title.partition(":")
            if not title:
                company, title = "", raw_title
            region = tag("region") or "Anywhere"
            body = tag("description")
            posted = ""
            m = re.search(r"<pubDate>(.*?)</pubDate>", it)
            if m:
                try:
                    posted = datetime.strptime(
                        m.group(1).strip()[:16], "%a, %d %b %Y"
                    ).strftime("%Y-%m-%d")
                except ValueError:
                    pass
            jobs.append(row(
                "wwr", title.strip(), company.strip(), region,
                tag("link"), posted, remote=True,
                us=us_status(region + " " + body[:400]), description=body,
            ))
        print(f"[wwr] {feed}: {len(items)} postings")
        time.sleep(1)
    return jobs


# ==========================================================================
# Source: Hacker News "Who is hiring?"
# ==========================================================================
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"


def crawl_hn(args):
    user = fetch_json("https://hacker-news.firebaseio.com/v0/user/whoishiring.json")
    if not user:
        print("[hn] could not read the whoishiring user")
        return []

    thread = None
    for sid in (user.get("submitted") or [])[:12]:
        item = fetch_json(HN_ITEM.format(sid)) or {}
        if re.search(r"who is hiring", item.get("title", ""), re.I):
            thread = item
            break
    if not thread:
        print("[hn] no 'Who is hiring?' thread found")
        return []

    kids = thread.get("kids", [])
    print(f"[hn] {thread.get('title')} — {len(kids)} top-level posts")

    def one(cid):
        c = fetch_json(HN_ITEM.format(cid), tries=2) or {}
        if not c or c.get("deleted") or c.get("dead") or not c.get("text"):
            return None
        body = strip_tags(c["text"])
        if not RELEVANT.search(body):
            return None
        headline = body.split("\n")[0][:150]
        company = re.split(r"\s*[|–-]\s*", headline)[0][:60]
        return row(
            "hn", headline, company, headline,
            f"https://news.ycombinator.com/item?id={c['id']}",
            datetime.fromtimestamp(c.get("time", 0), timezone.utc)
                .strftime("%Y-%m-%d") if c.get("time") else "",
            remote=bool(REMOTE_HINT.search(body)),
            us=us_status(body[:600]), description=body,
            # The role lives in the post body, not in a title field.
            match_text=body,
        )

    # The HN API refuses connections past ~4 concurrent clients, so this
    # stays deliberately modest and walks the thread in batches.
    jobs = []
    with futures.ThreadPoolExecutor(max_workers=4) as ex:
        for i in range(0, len(kids), 40):
            for res in ex.map(one, kids[i:i + 40]):
                if res:
                    jobs.append(res)
            time.sleep(1.0)
    print(f"  {len(jobs)} mention Android/mobile")
    return jobs


# ==========================================================================
# Sources: free public job APIs
# ==========================================================================
def crawl_remotive(args):
    """One request per query — 'search' takes a single string, not a list."""
    out, seen = [], set()
    for query in args.keywords:
        url = "https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode(
            {"search": query, "limit": 100}
        )
        rows = (fetch_json(url) or {}).get("jobs", [])
        print(f'[remotive] "{query}" -> {len(rows)} raw')
        for j in rows:
            jid = str(j.get("id", ""))
            if jid in seen:
                continue
            seen.add(jid)
            loc = j.get("candidate_required_location", "Remote")
            out.append(row(
                "remotive", j.get("title", ""), j.get("company_name", ""),
                loc, j.get("url", ""), (j.get("publication_date") or "")[:10],
                remote=True, description=strip_tags(j.get("description", "")),
                query=query,
            ))
        time.sleep(1)
    return out


def crawl_remoteok(args):
    data = fetch_json("https://remoteok.com/api") or []
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is a legal notice
        out.append(row(
            "remoteok", j.get("position", ""), j.get("company", ""),
            j.get("location") or "Remote", j.get("url", ""),
            (j.get("date") or "")[:10], remote=True,
            description=strip_tags(j.get("description", "")),
        ))
    print(f"[remoteok] {len(out)} postings")
    return out


def crawl_arbeitnow(args):
    out = []
    for page in range(1, min(args.pages, 5) + 1):
        data = fetch_json(
            "https://www.arbeitnow.com/api/job-board-api?page=%d" % page) or {}
        rows = data.get("data", [])
        if not rows:
            break
        for j in rows:
            if not j.get("remote"):
                continue
            ts = j.get("created_at")
            posted = (datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
                      if isinstance(ts, (int, float)) else "")
            out.append(row(
                "arbeitnow", j.get("title", ""), j.get("company_name", ""),
                j.get("location") or "Remote", j.get("url", ""), posted,
                remote=True, description=strip_tags(j.get("description", "")),
            ))
        time.sleep(1)
    print(f"[arbeitnow] {len(out)} remote postings")
    return out


# ==========================================================================
# Source: Lever company boards
# ==========================================================================
# Verified live: each token below answers the public v0 posting API with jobs.
# The list is deliberately short — Lever has no company index, so a slug is
# only discoverable by knowing the company uses Lever. `--discover` grows it.
LEVER_BOARDS = """
gopuff shieldai zoox ro anchorage cloudinary rigetti ledger
""".split()

LEVER_LIST = "https://api.lever.co/v0/postings/{}?mode=json"


def crawl_lever(args):
    """One request per company; each posting arrives whole.

    Lever states the workplace outright in categories.commitment/workplaceType
    on modern boards, and buries it in the location label on older ones, so
    both are read before falling back to the description.
    """
    boards = args.lever_boards or board_list("lever", LEVER_BOARDS, args)
    print(f"[lever] listing {len(boards)} company boards")

    def board(token):
        data = fetch_json(LEVER_LIST.format(token), tries=2)
        if not isinstance(data, list):
            return []
        out = []
        for j in data:
            title = (j.get("text") or "").strip()
            if not relevant(title, args):
                continue
            cats = j.get("categories") or {}
            label = cats.get("location") or ""
            # allLocations carries the remote alternates a single location
            # field hides — the same trap Ashby's secondaryLocations sets.
            extra = [x for x in (j.get("allLocations") or []) if x != label]
            if extra:
                label = " / ".join([label] + extra) if label else " / ".join(extra)
            workplace = (cats.get("workplaceType") or "").lower()
            body = strip_tags(j.get("descriptionPlain")
                              or j.get("description", ""))
            ts = j.get("createdAt")
            posted = (datetime.fromtimestamp(ts / 1000, timezone.utc)
                      .strftime("%Y-%m-%d")
                      if isinstance(ts, (int, float)) else "")
            out.append(row(
                "lever", title, token.title(), label or "Unspecified",
                j.get("hostedUrl", ""), posted,
                remote=(workplace == "remote")
                or bool(REMOTE_HINT.search(label))
                or (not workplace and bool(REMOTE_STRONG.search(body))),
                us=us_status(label) if label else None,
                description=body, apply_url=j.get("applyUrl", ""),
            ))
        return out

    listed = []
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)
    print(f"  {len(listed)} Android/mobile titles, "
          f"{sum(1 for j in listed if j['remote'])} of them remote")
    return listed


# ==========================================================================
# Source: Workable company boards
# ==========================================================================
# The documented v3 path (apply.workable.com/api/v3/accounts/…) needs a bearer
# token; this widget endpoint is the public one and needs nothing.
#
# Caveat worth knowing: an unknown slug 404s, but a real account that isn't
# hiring answers 200 with its name and an empty job list — so a bad entry here
# is quiet, not silent, and the two are told apart by whether "name" comes
# back at all. Most of the list below is currently in that second state.
WORKABLE_BOARDS = """
bolt paddle cloudtalk veriff hotjar aircall personio pleo sumup mollie
productboard mews contentful staffbase
""".split()

WORKABLE_LIST = "https://apply.workable.com/api/v1/widget/accounts/{}"


def crawl_workable(args):
    boards = args.workable_boards or board_list("workable", WORKABLE_BOARDS, args)
    print(f"[workable] listing {len(boards)} company boards")

    def board(token):
        data = fetch_json(WORKABLE_LIST.format(token), tries=2) or {}
        company = data.get("name") or token.title()
        out = []
        for j in data.get("jobs") or []:
            title = (j.get("title") or "").strip()
            if not relevant(title, args):
                continue
            city = j.get("city") or ""
            country = j.get("country") or ""
            label = ", ".join(x for x in (city, j.get("state") or "", country) if x)
            if j.get("telecommuting"):
                label = (label + " (Remote)").strip()
            body = strip_tags(j.get("description") or "")
            # The country field is authoritative here, the way Ashby's is.
            if country:
                status = "us" if country.strip().lower() in US_COUNTRY else "no"
            else:
                status = us_status(label)
            out.append(row(
                "workable", title, company, label or "Unspecified",
                j.get("url") or j.get("shortlink", ""),
                (j.get("published_on") or j.get("created_at") or "")[:10],
                remote=bool(j.get("telecommuting"))
                or bool(REMOTE_HINT.search(label)),
                us=status, description=body,
            ))
        return out

    listed = []
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)
    print(f"  {len(listed)} Android/mobile titles, "
          f"{sum(1 for j in listed if j['remote'])} of them remote")
    return listed


# ==========================================================================
# Source: SmartRecruiters company boards
# ==========================================================================
# Same silent-miss problem as Workable: an unknown company answers 200 with
# {"totalFound": 0} rather than 404, so a wrong slug looks like a quiet week.
SMARTRECRUITERS_BOARDS = "canva thales".split()

SR_LIST = ("https://api.smartrecruiters.com/v1/companies/{}/postings"
           "?limit=100&offset={}")
SR_JOB = "https://api.smartrecruiters.com/v1/companies/{}/postings/{}"


def crawl_smartrecruiters(args):
    """Listing pages carry the location but not the body, so — as with
    Greenhouse — only the Android/mobile hits pay for a second request."""
    boards = args.sr_boards or board_list("smartrecruiters", SMARTRECRUITERS_BOARDS, args)
    print(f"[smartrecruiters] listing {len(boards)} company boards")

    def board(token):
        out, offset = [], 0
        while offset < 400:               # a company with more than 400 open
            data = fetch_json(SR_LIST.format(token, offset), tries=2) or {}
            batch = data.get("content") or []
            if not batch:
                break
            for j in batch:
                title = (j.get("name") or "").strip()
                if not relevant(title, args):
                    continue
                loc = j.get("location") or {}
                label = ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                              loc.get("country")) if x)
                if loc.get("remote"):
                    label = (label + " (Remote)").strip()
                country = (loc.get("country") or "").strip().lower()
                out.append(row(
                    "smartrecruiters", title, token.title(),
                    label or "Unspecified",
                    "https://jobs.smartrecruiters.com/{}/{}".format(
                        token, j.get("id", "")),
                    (j.get("releasedDate") or "")[:10],
                    remote=bool(loc.get("remote"))
                    or bool(REMOTE_HINT.search(label)),
                    us=("us" if country in ("us", "united states")
                        else ("no" if country else us_status(label))),
                    sr_token=token, sr_id=j.get("id"),
                ))
            if len(batch) < 100:
                break
            offset += 100
        return out

    listed = []
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)

    known = getattr(args, "seen_keys", set())
    hits = [j for j in listed if job_key(j) not in known]

    def detail(j):
        data = fetch_json(SR_JOB.format(j.pop("sr_token"), j.pop("sr_id")),
                          tries=2)
        if data:
            ad = (data.get("jobAd") or {}).get("sections") or {}
            body = " ".join(
                strip_tags((ad.get(k) or {}).get("text") or "")
                for k in ("jobDescription", "qualifications", "companyDescription"))
            j["description"] = body[:2000]
            if not j["remote"]:
                j["remote"] = bool(REMOTE_STRONG.search(body))
        return j

    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        hits = list(ex.map(detail, hits))
    for j in listed:                       # drop bookkeeping from skipped rows
        j.pop("sr_token", None), j.pop("sr_id", None)
    print(f"  {len(listed)} Android/mobile titles, "
          f"{sum(1 for j in hits if j['remote'])} of them remote")
    return hits


# ==========================================================================
# Growing the ATS board lists
# ==========================================================================
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

# Words that live in a company's legal name and never in its ATS slug.
COMPANY_NOISE = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|plc|nv|bv|ag|sa|srl|"
    r"pty|holdings|group|company|technologies|solutions)\b\.?", re.I)


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


def probe_board(slug):
    """Which ATS hosts this slug with live jobs, or None if none of them do."""
    for ats in PROBE_ORDER:
        url, jobs_of = BOARD_PROBES[ats]
        if jobs_of(fetch_json(url.format(slug), tries=1, timeout=12)):
            return ats
    return None


def board_list(ats, builtin, args):
    """The built-in slugs for one ATS, plus whatever --discover has found."""
    found = getattr(args, "boards_found", None) or {}
    return list(dict.fromkeys(list(builtin) + list(found.get(ats, []))))


def known_slugs(boards):
    """Every slug already in a list, built-in or discovered.

    Seeded from the built-ins as well as the found ones: without that, a
    third of a first run is spent re-proving that Lyft is on Greenhouse.
    """
    known = {s for slugs in BUILTIN_BOARDS.values() for s in slugs}
    return known | {s for slugs in (boards.get("found") or {}).values()
                    for s in slugs}


def discover_boards(companies, boards, today):
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
        for slug, ats in zip(slugs, ex.map(probe_board, slugs)):
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


# ==========================================================================
# Source: Himalayas
# ==========================================================================
# Remote-only by construction, and the one aggregator here that states its
# location fence as a country list rather than prose — so US-ness is read,
# not guessed. Salary and seniority come structured too.
HIMALAYAS_API = "https://himalayas.app/jobs/api?limit=100"


def crawl_himalayas(args):
    # The feed is chronological with no keyword parameter and a hard 20 items
    # per page, so reaching an Android posting means walking a long way back:
    # --pages 5 covers 100 jobs of every discipline and finds nothing. Each
    # page here is worth a fifth of one elsewhere, so the budget is scaled.
    out, cursor, pages = [], None, max(10, min(args.pages * 5, 50))
    for _ in range(pages):
        url = HIMALAYAS_API + (f"&cursor={cursor}" if cursor else "")
        data = fetch_json(url, tries=2) or {}
        jobs = data.get("jobs") or []
        if not jobs:
            break
        for j in jobs:
            title = (j.get("title") or "").strip()
            if not relevant(title, args):
                continue
            fences = [str(x) for x in (j.get("locationRestrictions") or [])]
            label = ", ".join(fences) if fences else "Anywhere"
            if fences:
                low = [f.strip().lower() for f in fences]
                status = "us" if any(f in US_COUNTRY for f in low) else "no"
            else:
                status = "worldwide"
            ts = j.get("pubDate")
            posted = (datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
                      if isinstance(ts, (int, float)) else "")
            out.append(row(
                "himalayas", title, j.get("companyName", ""), label,
                j.get("applicationLink") or j.get("guid", ""), posted,
                remote=True, us=status,
                description=strip_tags(j.get("description")
                                       or j.get("excerpt") or ""),
                salary_min=j.get("minSalary"), salary_max=j.get("maxSalary"),
                salary_currency=j.get("currency") or "",
            ))
        cursor = data.get("nextCursor")
        if not cursor:
            break
        time.sleep(1)
    print(f"[himalayas] {len(out)} Android/mobile remote postings")
    return out


# ==========================================================================
# Sources that need an API key — free, but you have to register
# ==========================================================================
# Kept in the same shape as crawl_blocked(): with no key they explain
# themselves and return nothing, so the crawler still runs with zero keys.
KEYED = {
    "adzuna": ("ADZUNA_APP_ID + ADZUNA_APP_KEY",
               "free instantly at https://developer.adzuna.com/signup"),
    "usajobs": ("USAJOBS_KEY + USAJOBS_EMAIL",
                "free instantly at https://developer.usajobs.gov/apirequest/"),
    "jooble": ("JOOBLE_KEY",
               "emailed after review at https://jooble.org/api/about"),
}


def need_keys(name, *env):
    """Return the env values, or print why the source is unavailable."""
    vals = [os.environ.get(v, "").strip() for v in env]
    if all(vals):
        return vals
    missing = [v for v, got in zip(env, vals) if not got]
    want, how = KEYED[name]
    print(f"[{name}] unavailable: set {want} to enable it "
          f"({how}); missing {', '.join(missing)}")
    return None


# fetch()'s default Accept offers text/html before JSON, and Adzuna honours
# that literally: the same URL that returns jobs to curl returns its HTML docs
# page. Sources that content-negotiate need JSON asked for outright.
JSON_ONLY = {"Accept": "application/json"}

ADZUNA_SEARCH = ("https://api.adzuna.com/v1/api/jobs/us/search/{page}"
                 "?app_id={app_id}&app_key={app_key}&results_per_page=50"
                 "&what_phrase={phrase}&what={extra}&sort_by=date{age}")


def crawl_adzuna(args):
    """Adzuna carries salary — the only source here that does for most rows.

    It has no remote field at all, though: "remote" can only be asked for as
    a keyword, and the location that comes back is the company's city. So the
    remote call is made the same way LinkedIn's is — from the words — and the
    REMOTE_STRONG gate does the deciding.
    """
    keys = need_keys("adzuna", "ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    if not keys:
        return []
    app_id, app_key = keys
    age = f"&max_days_old={args.days}" if args.days else ""
    out = []
    for q in args.keywords:
        got = 0
        for page in range(1, max(1, min(args.pages, 10)) + 1):
            data = fetch_json(ADZUNA_SEARCH.format(
                page=page, app_id=app_id, app_key=app_key,
                phrase=urllib.parse.quote(q), extra="remote", age=age),
                tries=2, headers=JSON_ONLY)
            results = (data or {}).get("results") or []
            if not results:
                break
            for j in results:
                title = (j.get("title") or "").strip()
                title = strip_tags(title)
                if not relevant(title, args):
                    continue
                body = strip_tags(j.get("description") or "")
                label = (j.get("location") or {}).get("display_name", "")
                # Adzuna's own salary is often a model estimate rather than
                # the posting's number; the flag says which, and a guess is
                # not worth reporting as fact.
                predicted = str(j.get("salary_is_predicted") or "") == "1"
                out.append(row(
                    "adzuna", title,
                    (j.get("company") or {}).get("display_name", ""),
                    label, j.get("redirect_url", ""),
                    (j.get("created") or "")[:10],
                    remote=bool(REMOTE_HINT.search(title))
                    or bool(REMOTE_STRONG.search(body)),
                    # /jobs/us/ is the US index — a posting that reached
                    # it is US-based whatever its city string looks like.
                    us=("no" if us_status(label) == "no" else "us"),
                    description=body,
                    salary_min=None if predicted else j.get("salary_min"),
                    salary_max=None if predicted else j.get("salary_max"),
                    salary_currency="" if predicted else "USD",
                    salary_predicted="yes" if predicted else "no",
                    query=q,
                ))
                got += 1
            if len(results) < 50:
                break
            time.sleep(1)
        print(f'[adzuna] "{q}": {got} matches')
    return out


# RemoteIndicator is case-sensitive in a way the docs don't call out:
# "true" filters to remote postings, "True" silently matches nothing at all
# rather than erroring — an empty result set that looks like a quiet week.
USAJOBS_SEARCH = ("https://data.usajobs.gov/api/search?Keyword={kw}"
                  "&ResultsPerPage=100&RemoteIndicator=true")


def crawl_usajobs(args):
    """Federal postings. Low volume for mobile work — single digits is normal
    — but every hit is genuinely remote-flagged and unambiguously US."""
    keys = need_keys("usajobs", "USAJOBS_KEY", "USAJOBS_EMAIL")
    if not keys:
        return []
    key, email = keys
    # The registered email doubles as the User-Agent; a mismatch is a 401
    # even when the key itself is right.
    headers = {"Host": "data.usajobs.gov", "User-Agent": email,
               "Authorization-Key": key, "Accept": "application/json"}
    out = []
    for q in args.keywords:
        data = fetch_json(USAJOBS_SEARCH.format(kw=urllib.parse.quote(q)),
                          tries=2, headers=headers)
        items = (((data or {}).get("SearchResult") or {})
                 .get("SearchResultItems") or [])
        got = 0
        for it in items:
            j = it.get("MatchedObjectDescriptor") or {}
            title = (j.get("PositionTitle") or "").strip()
            if not relevant(title, args):
                continue
            locs = [l.get("LocationName", "")
                    for l in (j.get("PositionLocation") or [])]
            label = " / ".join(dict.fromkeys(x for x in locs if x)) or "United States"
            # Federal pay is quoted per year or per hour, and RateIntervalCode
            # says which; both are reported here as yearly dollars.
            pay = (j.get("PositionRemuneration") or [{}])[0]
            rate = pay.get("RateIntervalCode", "")
            low = annualise(pay.get("MinimumRange"), rate)
            high = annualise(pay.get("MaximumRange"), rate)
            summary = (j.get("UserArea", {}).get("Details", {})
                       .get("JobSummary", "")) or ""
            out.append(row(
                "usajobs", title,
                (j.get("OrganizationName") or j.get("DepartmentName") or ""),
                label, j.get("PositionURI", ""),
                (j.get("PublicationStartDate") or "")[:10],
                remote=True, us="us", description=strip_tags(summary),
                salary_min=low, salary_max=high,
                salary_currency="USD" if low else "",
                query=q,
            ))
            got += 1
        print(f'[usajobs] "{q}": {got} matches')
        time.sleep(1)
    return out


JOOBLE_API = "https://jooble.org/api/{}"


def crawl_jooble(args):
    """Jooble is an aggregator: its links point back at the board that
    originated the posting, so expect overlap with LinkedIn and Adzuna."""
    keys = need_keys("jooble", "JOOBLE_KEY")
    if not keys:
        return []
    key, = keys
    out = []
    for q in args.keywords:
        # Jooble has no remote field and no remote filter, so — as with
        # Adzuna — the word goes in the query and REMOTE_STRONG decides.
        body = json.dumps({"keywords": q + " remote",
                           "location": args.location,
                           "page": "1"}).encode("utf-8")
        req = urllib.request.Request(
            JOOBLE_API.format(key), data=body,
            headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"  ! jooble {type(e).__name__}: {e}", file=sys.stderr)
            continue
        got = 0
        for j in data.get("jobs") or []:
            title = (j.get("title") or "").strip()
            if not relevant(title, args):
                continue
            label = j.get("location") or ""
            snippet = strip_tags(j.get("snippet") or "")
            lo, hi = parse_salary(j.get("salary") or "")
            out.append(row(
                "jooble", title, j.get("company", ""), label,
                j.get("link", ""), (j.get("updated") or "")[:10],
                remote=bool(REMOTE_HINT.search(title + " " + label))
                or bool(REMOTE_STRONG.search(snippet)),
                us=us_status(label), description=snippet,
                salary_min=lo, salary_max=hi,
                salary_currency="USD" if lo else "",
                query=q,
            ))
            got += 1
        print(f'[jooble] "{q}": {got} matches')
        time.sleep(1)
    return out


# ==========================================================================
# Sources that cannot be crawled — kept so they explain themselves
# ==========================================================================
BLOCKED = {
    "indeed": "HTTP 403 — Cloudflare bot wall; Indeed also retired its public API.",
    "wellfound": "Cloudflare Turnstile challenge; the HTML carries no job data.",
    "dice": "Search API returns 403 — the public frontend key has been rotated.",
    "hired": "Hired.com no longer exists — it now redirects to LHH.",
    "jobright": "Server-rendered results ignore the search keyword — asking for "
                "'android developer' returns unrelated marketing roles. The real "
                "results come from an API that requires a logged-in account.",
}


def crawl_blocked(name):
    def _fn(args):
        print(f"[{name}] unavailable: {BLOCKED[name]}")
        return []
    return _fn


SOURCES = {
    "linkedin": crawl_linkedin,
    "greenhouse": crawl_greenhouse,
    "ashby": crawl_ashby,
    "lever": crawl_lever,
    "workable": crawl_workable,
    "smartrecruiters": crawl_smartrecruiters,
    "builtin": crawl_builtin,
    "arc": crawl_arc,
    "wwr": crawl_wwr,
    "hn": crawl_hn,
    "remotive": crawl_remotive,
    "remoteok": crawl_remoteok,
    "arbeitnow": crawl_arbeitnow,
    "himalayas": crawl_himalayas,
    "adzuna": crawl_adzuna,
    "usajobs": crawl_usajobs,
    "jooble": crawl_jooble,
}
SOURCES.update({n: crawl_blocked(n) for n in BLOCKED})

# Two sources rarely spell one job the same way. What varies between them is
# punctuation and a trailing workplace or location — "Mobile Engineer II
# (Android)" against "Mobile Engineer II, Android", "Reddit, Inc." against
# "Reddit" — so those are normalised away before the two are compared.
#
# Deliberately NOT normalised: seniority ("Senior Android Engineer" is not
# "Android Engineer"), a team or product in brackets ("(Payments)" is a
# different job), and employment type (a contract post and a permanent one
# are two openings). Dedupe that merges too much loses postings silently,
# which is worse than reporting one twice.
TITLE_SUFFIX = re.compile(
    r"[\s,]*[\(\[\-–—|]*\s*"
    r"\b(remote(\s*[-,(]?\s*(us|usa|united\s+states|anywhere))?|"
    r"us|usa|united\s+states|hybrid|on-?site|in-?office|"
    r"[mwfdxhn](\s*/\s*[mwfdxhn])+)\b"       # (m/f/d), (w/m/x) …
    r"\s*[\)\]]*\s*$", re.I)


def dedupe_key(job):
    """(title, company) reduced to what two sources would agree on."""
    title = unescape(job.get("title") or "").strip()
    for _ in range(3):                    # "Android Engineer (Remote) - US"
        shorter = TITLE_SUFFIX.sub("", title).strip()
        if shorter == title or not shorter:
            break
        title = shorter
    company = COMPANY_NOISE.sub(" ", unescape(job.get("company") or "").lower())
    flat = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    # Falling back to the raw field keeps a company literally called "Group"
    # from colliding with every other one that normalises to nothing.
    return (flat(title) or (job.get("title") or "").lower().strip(),
            flat(company) or (job.get("company") or "").lower().strip())


# Which link survives when two sources carry the same job. A company's own
# ATS is authoritative about location and stays live after the aggregators
# have rotated the posting out; an aggregator's redirect is the worst link to
# keep, so it ranks last.
SOURCE_RANK = {
    "greenhouse": 1, "ashby": 1, "lever": 1, "workable": 1,
    "smartrecruiters": 1, "usajobs": 2, "himalayas": 3, "wwr": 4,
    "builtin": 5, "arc": 5, "remoteok": 5, "arbeitnow": 5, "remotive": 5,
    "hn": 6, "linkedin": 7, "adzuna": 8, "jooble": 9,
}

DEFAULT_SOURCES = ["linkedin", "greenhouse", "ashby", "lever", "workable",
                   "smartrecruiters", "himalayas", "adzuna", "usajobs",
                   "builtin", "arc", "wwr", "hn"]


# ==========================================================================
# Seen-job state — so a repeat run only reports what's new
# ==========================================================================
# The boards give no "changed since" parameter, so a run always re-fetches the
# same listings; what this avoids is re-reporting them. Identity is the job
# URL, which is stable across runs on every source here.
def job_key(job):
    # Keep the query string: several boards carry the job id there
    # (…/jobs/?gh_jid=4916795), so stripping it merges unrelated postings.
    url = (job.get("url") or "").strip().rstrip("/")
    if url:
        return url
    return "{}|{}|{}".format(job["source"], job["title"].lower().strip(),
                             job["company"].lower().strip())


META = "_meta"  # run bookkeeping, stored alongside the seen jobs


def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, ensure_ascii=False)


def catchup_days(state, wanted, today):
    """How far back this run actually needs to look.

    After a 60-day sweep there is no reason to ask for 60 days again the
    next morning — only for what appeared since. Two spare days absorb
    postings that land late or shift timezone.
    """
    last = (state.get(META) or {}).get("last_run")
    if not last:
        return wanted
    try:
        gap = (datetime.strptime(today, "%Y-%m-%d")
               - datetime.strptime(last, "%Y-%m-%d")).days
    except ValueError:
        return wanted
    return max(1, min(wanted, gap + 2))


# ==========================================================================
# Output
# ==========================================================================
COLUMNS = ["source", "title", "company", "location", "us", "posted",
           "first_seen", "easy_apply", "salary_min", "salary_max",
           "salary_currency", "salary_predicted", "url", "apply_url",
           "query", "description"]


SALARY_FIELDS = ("salary_min", "salary_max", "salary_currency",
                 "salary_predicted")

REJECTED_COLUMNS = ["source", "reason", "title", "company", "location",
                    "posted", "url"]


def report_rejections(rejected, base):
    """Write every dropped posting next to the rule that dropped it.

    The gate is otherwise silent, and silence is ambiguous: a run that
    returns 12 jobs out of 900 looks the same whether it was a quiet week or
    a regex that has stopped matching. This is also the cheap way to audit
    the remote call, which is the crawl's weakest link — sort the file by
    reason, read the "onsite" and "not-remote" rows, and the false-negative
    rate is right there instead of being sampled by hand.
    """
    rows = [{"source": src, "reason": "not-mobile: dropped by the source's "
                                      "own title gate, before the main one",
             "title": title, "company": "", "location": "",
             "posted": "", "url": ""}
            for src, title in _SKIPPED]
    rows += [dict({k: str(job.get(k, "") or "") for k in REJECTED_COLUMNS},
                  reason=why)
             for job, why in rejected]

    path = base + "_rejected.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REJECTED_COLUMNS,
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    by_rule = {}
    for r in rows:
        rule = r["reason"].split(":", 1)[0]
        by_rule.setdefault(rule, {})
        by_rule[rule][r["source"]] = by_rule[rule].get(r["source"], 0) + 1

    print(f"\n[why] {len(rows)} postings rejected "
          f"({len(_SKIPPED)} of them by a source's own title gate)")
    for rule, sources in sorted(by_rule.items(),
                                key=lambda kv: -sum(kv[1].values())):
        top = ", ".join(f"{s} {n}" for s, n in
                        sorted(sources.items(), key=lambda kv: -kv[1])[:4])
        print(f"  {rule:<13}{sum(sources.values()):>6}   {top}")
    print(f"  -> {path}")


BOOKKEEPING = ("match_text", "gh_token", "gh_id", "sr_token", "sr_id")


def strip_bookkeeping(jobs):
    """Drop the per-source scratch fields; they are not worth writing out."""
    for j in jobs:
        for k in BOOKKEEPING:
            j.pop(k, None)
    return jobs


def load_archive(path):
    """Every posting the crawler has ever matched. A missing file is empty."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue        # a half-written line from a killed run
    except FileNotFoundError:
        pass
    return out


def append_archive(path, jobs):
    """Add whatever this run matched that the archive has not seen before.

    Without this the full record exists nowhere. write_outputs() rewrites its
    three files from scratch every run and — unless --include-seen — is handed
    only the *new* postings, so yesterday's CSV is gone; and the seen-state
    keeps a title, a company and a date, not a location, salary or link. One
    line per posting, appended and never rewritten, so an interrupted run can
    at worst lose its last line rather than the file.
    """
    known = {job_key(j) for j in load_archive(path)}
    added = [j for j in jobs if job_key(j) not in known]
    if added:
        with open(path, "a", encoding="utf-8") as fh:
            for j in added:
                fh.write(json.dumps(j, ensure_ascii=False) + "\n")
    return len(added)


def write_outputs(jobs, base):
    strip_bookkeeping(jobs)

    with open(base + ".csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(jobs)
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(jobs, fh, indent=2, ensure_ascii=False)
    with open(base + "_links.txt", "w", encoding="utf-8") as fh:
        fh.writelines(j["url"] + "\n" for j in jobs)


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
    if args.why:
        report_rejections(rejected, args.out)
    print()

    for j in jobs:
        tag = {"yes": "[easy]", "no": "[site]"}.get(j["easy_apply"], "")
        print(f"{j['source']:<11}{(j['posted'] or '?'):<12}"
              f"{j['title'][:42]:<44}{j['company'][:18]:<20}{tag:<7}{j['url']}")


if __name__ == "__main__":
    main()
