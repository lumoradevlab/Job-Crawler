"""Jooble — an aggregator, reached by POSTing a JSON query."""

import json
import sys
import time
import urllib.request

from ...filters.geo import us_status
from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT, REMOTE_STRONG
from ...models import row
from ...net.http import UA
from ...parse.html import strip_tags
from ...parse.salary import parse_salary
from .base import need_keys


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
