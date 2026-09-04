"""We Work Remotely, via its per-category RSS feeds."""

import re
import time
from datetime import datetime

from ...filters.geo import us_status
from ...models import row
from ...parse.html import strip_tags


WWR_FEEDS = [
    "remote-programming-jobs",
    "remote-full-stack-programming-jobs",
    "remote-back-end-programming-jobs",
    "remote-front-end-programming-jobs",
]


def crawl_wwr(cfg, ctx):
    jobs = []
    for feed in WWR_FEEDS:
        xml = ctx.fetch.get(f"https://weworkremotely.com/categories/{feed}.rss")
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
        ctx.report.source("wwr", f"{feed}: {len(items)} postings")
    return jobs
