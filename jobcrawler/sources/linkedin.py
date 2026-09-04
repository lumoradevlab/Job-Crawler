"""LinkedIn, via the public "guest" job search endpoint (no login)."""

import random
import re
import time
import urllib.parse
from html.parser import HTMLParser

from ..filters.rules import relevant
from ..filters.workplace import ONSITE_STRONG, REMOTE_STRONG
from ..models import row
from ..parse.html import strip_tags
from ..store.seen import job_key


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


def linkedin_detail(job_id, ctx):
    """Description, Easy Apply flag and external apply URL for one posting.

    LinkedIn marks the apply button 'apply-link-onsite' for Easy Apply and
    'apply-link-offsite' when it hands you off to the company's own site.
    """
    html = ctx.fetch.get(LINKEDIN_DETAIL.format(job_id))
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


def crawl_linkedin(cfg, ctx):
    """Run every keyword query, paging until a query runs dry."""
    seen, jobs = set(), []

    for query in cfg.keywords:
        params = {
            "keywords": query,
            "location": cfg.location,
            "f_WT": WORKPLACE_REMOTE,
            "sortBy": "DD",  # most recent first
        }
        geo = GEO_IDS.get(cfg.location.strip().lower())
        if geo:
            params["geoId"] = geo
        if cfg.days:
            params["f_TPR"] = "r%d" % (cfg.days * 86400)
        if cfg.level:
            params["f_E"] = EXPERIENCE[cfg.level]

        ctx.report.source("linkedin", f'query: "{query}"')
        for page in range(cfg.pages):
            params["start"] = page * 10
            html = ctx.fetch.get(LINKEDIN_SEARCH + "?" + urllib.parse.urlencode(params))
            if not html.strip():
                ctx.report.detail(f"page {page + 1}: empty — end of this query")
                break

            parser = JobCardParser()
            parser.feed(html)
            parser.close()
            if not parser.jobs:
                ctx.report.detail(f"page {page + 1}: 0 cards — end of this query")
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
            ctx.report.detail(f"page {page + 1}: {len(parser.jobs)} cards, "
                  f"+{new} new (running total {len(jobs)})")

    if cfg.details:
        # One request per posting, so only pay it for jobs we haven't read.
        known = ctx.seen_keys
        todo = [j for j in jobs if job_key(j) not in known]
        ctx.report.source("linkedin", f"fetching details for {len(todo)} new jobs "
              f"(~{len(todo) * cfg.delay / 60:.0f} min)")
        for i, job in enumerate(todo, 1):
            job.update(linkedin_detail(job["job_id"], ctx))
            if i % 10 == 0:
                ctx.report.detail(f"{i}/{len(todo)}")
    return jobs
