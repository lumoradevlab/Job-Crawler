"""Arbeitnow's free public job-board API."""

import time
from datetime import datetime, timezone

from ...models import row
from ...parse.html import strip_tags


def crawl_arbeitnow(cfg, ctx):
    out = []
    for page in range(1, min(cfg.pages, 5) + 1):
        data = ctx.fetch.get_json(
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
    print(f"[arbeitnow] {len(out)} remote postings")
    return out
