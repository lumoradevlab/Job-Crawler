"""Remotive's free public job API."""

import time
import urllib.parse

from ...models import row
from ...net.http import fetch_json
from ...parse.html import strip_tags


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
