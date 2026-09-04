"""Himalayas — remote-only, and states its location fence as a country list."""

import time
from datetime import datetime, timezone

from ...filters.geo import US_COUNTRY
from ...filters.rules import relevant
from ...models import row
from ...parse.html import strip_tags


# Remote-only by construction, and the one aggregator here that states its
# location fence as a country list rather than prose — so US-ness is read,
# not guessed. Salary and seniority come structured too.
HIMALAYAS_API = "https://himalayas.app/jobs/api?limit=100"


def crawl_himalayas(cfg, ctx):
    # The feed is chronological with no keyword parameter and a hard 20 items
    # per page, so reaching an Android posting means walking a long way back:
    # --pages 5 covers 100 jobs of every discipline and finds nothing. Each
    # page here is worth a fifth of one elsewhere, so the budget is scaled.
    out, cursor, pages = [], None, max(10, min(cfg.pages * 5, 50))
    for _ in range(pages):
        url = HIMALAYAS_API + (f"&cursor={cursor}" if cursor else "")
        data = ctx.fetch.get_json(url, tries=2) or {}
        jobs = data.get("jobs") or []
        if not jobs:
            break
        for j in jobs:
            title = (j.get("title") or "").strip()
            if not relevant(title, cfg.filters, "himalayas"):
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
    print(f"[himalayas] {len(out)} Android/mobile remote postings")
    return out
