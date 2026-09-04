"""arc.dev's remote board (Next.js payload)."""

import time
import urllib.parse
from datetime import datetime, timezone

from ...models import row
from ...parse.html import next_data


def crawl_arc(cfg, ctx):
    jobs = []
    for q in cfg.keywords:
        url = "https://arc.dev/remote-jobs?" + urllib.parse.urlencode({"search": q})
        props = next_data(ctx.fetch.get(url))
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
    return jobs
