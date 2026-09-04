"""Google Jobs, via SerpApi — the widest net here."""

import re
import sys
import time
import urllib.parse

from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT
from ...models import row
from ...parse.dates import relative_date
from ...parse.html import strip_tags
from ...parse.salary import google_salary
from .base import JSON_ONLY, need_keys


# The widest net here: Google Jobs indexes the aggregators and the company
# boards alike. Google publishes no API for it, so this goes through SerpApi.
#
# Two things earn it the key. It is the only source that states remote as a
# structured boolean — detected_extensions.work_from_home — rather than
# leaving it to be read out of prose the way LinkedIn, Adzuna and Jooble do.
# And each result carries source_link, the posting on whoever published it,
# so what gets kept is not a Google redirect.
#
# The cost is the catch: SerpApi bills one search per keyword per page, and
# the free tier is 250 searches a MONTH. A default 8-keyword run at one page
# spends 8 of them, which is why this source is off by default, pages are
# capped hard, and every run prints what it spent. serpapi.com/account reports
# the balance and is itself free, so checking costs nothing.
SERPAPI_SEARCH = "https://serpapi.com/search"
SERPAPI_MAX_PAGES = 3


def crawl_serpapi(cfg, ctx):
    """Google Jobs results, one SerpApi search per keyword per page."""
    keys = need_keys("serpapi", "SERPAPI_KEY")
    if not keys:
        return []
    key, = keys
    pages = max(1, min(cfg.pages, SERPAPI_MAX_PAGES))
    out, spent = [], 0

    for q in cfg.keywords:
        token, got = None, 0
        for _ in range(pages):
            params = {"engine": "google_jobs", "q": q + " remote",
                      "google_domain": "google.com", "hl": "en", "gl": "us",
                      "api_key": key}
            # SerpApi rejects a location it cannot resolve, and "Worldwide"
            # is not one of its places — so --anywhere drops the parameter
            # rather than erroring the whole source out.
            if cfg.location.strip().lower() not in ("worldwide", "anywhere"):
                params["location"] = cfg.location
            if token:
                params["next_page_token"] = token
            data = ctx.fetch.get_json(
                SERPAPI_SEARCH + "?" + urllib.parse.urlencode(params),
                tries=2, headers=JSON_ONLY)
            spent += 1
            if not data:
                break
            # Quota exhaustion and a bad key both arrive as a 200 with an
            # "error" string, so every further search would be wasted too.
            if data.get("error"):
                print(f"  ! serpapi: {data['error']} "
                      f"({spent} searches spent)", file=sys.stderr)
                return out

            for j in data.get("jobs_results") or []:
                title = (j.get("title") or "").strip()
                if not relevant(title, cfg.filters, "serpapi"):
                    continue
                det = j.get("detected_extensions") or {}
                first = (j.get("apply_options") or [{}])[0]
                # share_link is a google.com search URL and the worst of the
                # three, so it is only reached for when the others are absent.
                link = (j.get("source_link") or first.get("link")
                        or j.get("share_link") or "")
                where = (j.get("location") or "").strip()
                lo, hi = google_salary(det.get("salary") or "")
                out.append(row(
                    "serpapi", title, j.get("company_name", ""),
                    where or "Unspecified", link,
                    relative_date(det.get("posted_at", "")),
                    remote=bool(det.get("work_from_home"))
                    or bool(REMOTE_HINT.search(title + " " + where)),
                    description=strip_tags(j.get("description") or ""),
                    apply_url=first.get("link", ""),
                    salary_min=lo, salary_max=hi,
                    salary_currency="USD" if lo else "",
                    query=q,
                ))
                got += 1

            token = (data.get("serpapi_pagination") or {}).get("next_page_token")
            if not token:
                break
        print(f'[serpapi] "{q}": {got} matches')
    print(f"  {len(out)} postings, {spent} SerpApi search"
          f"{'' if spent == 1 else 'es'} spent (free tier is 250 a month)")
    return out
