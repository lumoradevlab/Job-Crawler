"""USAJOBS — the federal job board's official API."""

import time
import urllib.parse

from ...filters.rules import relevant
from ...models import row
from ...parse.html import strip_tags
from ...parse.salary import annualise
from .base import need_keys


# RemoteIndicator is case-sensitive in a way the docs don't call out:
# "true" filters to remote postings, "True" silently matches nothing at all
# rather than erroring — an empty result set that looks like a quiet week.
USAJOBS_SEARCH = ("https://data.usajobs.gov/api/search?Keyword={kw}"
                  "&ResultsPerPage=100&RemoteIndicator=true")


def crawl_usajobs(cfg, ctx):
    """Federal postings. Low volume for mobile work — single digits is normal
    — but every hit is genuinely remote-flagged and unambiguously US."""
    keys = need_keys("usajobs", "USAJOBS_KEY", "USAJOBS_EMAIL", report=ctx.report)
    if not keys:
        return []
    key, email = keys
    # The registered email doubles as the User-Agent; a mismatch is a 401
    # even when the key itself is right.
    headers = {"Host": "data.usajobs.gov", "User-Agent": email,
               "Authorization-Key": key, "Accept": "application/json"}
    out = []
    for q in cfg.keywords:
        data = ctx.fetch.get_json(USAJOBS_SEARCH.format(kw=urllib.parse.quote(q)),
                          tries=2, headers=headers)
        items = (((data or {}).get("SearchResult") or {})
                 .get("SearchResultItems") or [])
        got = 0
        for it in items:
            j = it.get("MatchedObjectDescriptor") or {}
            title = (j.get("PositionTitle") or "").strip()
            if not relevant(title, cfg.filters, "usajobs", ctx.report):
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
        ctx.report.source("usajobs", f'"{q}": {got} matches')
    return out
