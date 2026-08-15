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

US_HINT = re.compile(
    r"\b(united\s+states|u\.?s\.?a\.?|usa|us[\s-]+only|us[\s-]+based|"
    r"anywhere\s+in\s+the\s+us|remote\s*[-,]?\s*us|nationwide)\b", re.I
)
WORLDWIDE = re.compile(
    r"\b(anywhere|worldwide|global|remote\s*[-,]?\s*global|"
    r"no\s+preference|any\s+location)\b", re.I
)
NON_US = re.compile(
    r"\b(emea|apac|latam|europe|european|uk\b|united\s+kingdom|ireland|"
    r"germany|france|spain|portugal|poland|netherlands|india|pakistan|"
    r"philippines|singapore|australia|canada|brazil|argentina|mexico\s+city|"
    r"nigeria|kenya|japan|china|korea|vietnam|indonesia|turkey|romania|"
    r"ukraine|serbia|bulgaria|czech|hungary|greece|israel|uae|dubai)\b", re.I
)
# "Remote (CET ±3)" style timezone fences that rule out a US-based applicant.
NON_US_TZ = re.compile(r"\b(cet|cest|eet|eest|bst|ist|gmt\s*[+±])\b", re.I)


def us_status(text):
    """Grade a location string: 'us', 'worldwide', 'no', or 'unknown'."""
    if not text:
        return "unknown"
    t = text.strip()
    if NON_US_TZ.search(t) and not US_HINT.search(t):
        return "no"
    if US_HINT.search(t):
        return "us"
    if set(re.findall(r",\s*([A-Z]{2})\b", t)) & ABBREV:
        return "us"
    low = t.lower()
    if any(s in low for s in STATES):
        return "us"
    if WORLDWIDE.search(t):
        return "worldwide"
    if NON_US.search(t):
        return "no"
    return "unknown"


def keep(job, args):
    """The single gate every posting must pass, whatever its source."""
    title = job["title"]
    # Structured boards put the role in the title; free-text sources (HN)
    # bury it in the body, so they set match_text to widen the gate.
    subject = job.get("match_text") or title

    if not args.no_filter and not (RELEVANT.search(subject)
                                   and ROLE.search(subject)):
        return False
    if args.must:
        hay = (title + " " + job.get("description", "")).lower()
        if not all(w.lower() in hay for w in args.must):
            return False
    if args.exclude and any(w.lower() in title.lower() for w in args.exclude):
        return False
    if args.easy_apply_only and job.get("easy_apply") != "yes":
        return False
    if not job.get("remote"):
        return False
    # A title that names the workplace outranks whatever the board's own
    # remote filter claimed — unless it offers both ("Remote or Hybrid").
    where = title + " " + job.get("location", "")
    if ONSITE.search(where) and not REMOTE_HINT.search(where):
        return False

    if not args.anywhere:
        status = job.get("us") or us_status(job.get("location", ""))
        if status == "no":
            return False
        if args.strict_us and status != "us":
            return False

    if args.days and job.get("posted"):
        try:
            posted = datetime.strptime(job["posted"][:10], "%Y-%m-%d")
            if posted < datetime.now() - timedelta(days=args.days):
                return False
        except ValueError:
            pass
    return True


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
    boards = args.boards or GREENHOUSE_BOARDS
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

    hits = [j for j in listed
            if args.no_filter or (RELEVANT.search(j["title"])
                                  and ROLE.search(j["title"]))]
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
    boards = args.ashby_boards or ASHBY_BOARDS
    print(f"[ashby] listing {len(boards)} company boards")

    def board(token):
        data = fetch_json(ASHBY_LIST.format(token), tries=2) or {}
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            title = j.get("title", "").strip()
            if not (args.no_filter or (RELEVANT.search(title)
                                       and ROLE.search(title))):
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
    "builtin": crawl_builtin,
    "arc": crawl_arc,
    "wwr": crawl_wwr,
    "hn": crawl_hn,
    "remotive": crawl_remotive,
    "remoteok": crawl_remoteok,
    "arbeitnow": crawl_arbeitnow,
}
SOURCES.update({n: crawl_blocked(n) for n in BLOCKED})

DEFAULT_SOURCES = ["linkedin", "greenhouse", "ashby", "builtin", "arc", "wwr", "hn"]


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
           "first_seen", "easy_apply", "url", "apply_url", "query",
           "description"]


def write_outputs(jobs, base):
    for j in jobs:  # internal bookkeeping, not worth writing out
        for k in ("match_text", "gh_token", "gh_id"):
            j.pop(k, None)

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
    p.add_argument("--details", action="store_true",
                   help="fetch each LinkedIn posting: description + Easy Apply")
    p.add_argument("--easy-apply-only", action="store_true",
                   help="keep only LinkedIn Easy Apply jobs (needs --details)")
    p.add_argument("--must", nargs="+", metavar="WORD",
                   help="keep only jobs containing all of these words")
    p.add_argument("--exclude", nargs="+", metavar="WORD",
                   help="drop jobs whose title contains any of these")
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
    args.seen_keys = set() if args.include_seen else {
        k for k in state if k != META
    }

    if not args.full and args.days:
        narrowed = catchup_days(state, args.days, today)
        if narrowed != args.days:
            print(f"catching up: asking for the last {narrowed} days instead "
                  f"of {args.days} (last run {state[META]['last_run']}); "
                  f"--full re-sweeps the whole window")
            args.days = narrowed

    collected = []
    for name in args.source:
        try:
            collected.extend(SOURCES[name](args))
        except KeyboardInterrupt:
            print("\ninterrupted — writing what we have")
            break
        except Exception as e:                      # keep other sources alive
            print(f"  ! {name} failed: {type(e).__name__}: {e}", file=sys.stderr)

    seen, jobs = set(), []
    for j in collected:
        key = (j["title"].lower().strip(), j["company"].lower().strip())
        if key in seen or not keep(j, args):
            continue
        seen.add(key)
        j["us"] = j.get("us") or us_status(j.get("location", ""))
        jobs.append(j)
    jobs.sort(key=lambda j: (j.get("posted") or "", j["source"]), reverse=True)

    # Split into new vs already-reported, then remember everything we saw.
    fresh = []
    for j in jobs:
        prior = state.get(job_key(j))
        j["first_seen"] = (prior or {}).get("first_seen", today)
        if prior is None:
            fresh.append(j)
    total_matched = len(jobs)
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
    if by_source:
        print("  " + ", ".join(f"{k}: {v}" for k, v in sorted(by_source.items())))
    if jobs and not args.anywhere:
        named = sum(1 for j in jobs if j["us"] == "us")
        print(f"  {named} name a US location, {len(jobs) - named} are "
              f'"worldwide"/unlabelled (use --strict-us to drop those)')
    if args.details:
        print(f"  {sum(1 for j in jobs if j['easy_apply'] == 'yes')} are Easy Apply")
    print()

    for j in jobs:
        tag = {"yes": "[easy]", "no": "[site]"}.get(j["easy_apply"], "")
        print(f"{j['source']:<11}{(j['posted'] or '?'):<12}"
              f"{j['title'][:42]:<44}{j['company'][:18]:<20}{tag:<7}{j['url']}")


if __name__ == "__main__":
    main()
