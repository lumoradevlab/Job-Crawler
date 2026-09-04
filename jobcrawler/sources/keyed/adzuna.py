"""Adzuna — the one source that carries salary for most rows."""

import time
import urllib.parse

from ...filters.geo import us_status
from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT, REMOTE_STRONG
from ...models import row
from ...net.http import fetch_json
from ...parse.html import strip_tags
from .base import JSON_ONLY, need_keys


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
        print(f'[adzuna] "{q}": {got} matches')
    return out
