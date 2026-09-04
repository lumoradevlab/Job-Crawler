"""Built In's remote board (server-rendered job cards)."""

import re
import time
import urllib.parse
from html import unescape

from ...filters.geo import us_status
from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT
from ...models import row
from ...parse.dates import relative_date
from ...parse.html import strip_tags


BUILTIN_URL = "https://builtin.com/jobs/remote/dev-engineering"

# One card per posting. The fields hang off data-id attributes and off the
# icon that precedes each value, which is what these patterns anchor to.
BI_CARD = 'data-id="job-card"'
BI_TITLE = re.compile(r'data-id="job-card-title"[^>]*>(.*?)</a>', re.S)
BI_ALIAS = re.compile(r'data-alias="([^"]+)"')
BI_COMPANY = re.compile(r'data-id="company-title"[^>]*>\s*<span>(.*?)</span>', re.S)
# The value sometimes sits in a bare <div> wrapper and sometimes doesn't,
# so both patterns tolerate it — without it the country is silently lost and
# a Berlin posting reads as an unlabelled "Remote".
BI_VALUE = r'[^>]*></i>\s*</div>\s*(?:<div>\s*)?<span[^>]*>([^<]*)</span>'
BI_WORKPLACE = re.compile("fa-house-building" + BI_VALUE)
BI_LOCATION = re.compile("fa-location-dot" + BI_VALUE)
# Cards open to several cities list them in a tooltip instead of the span.
BI_LOCATIONS = re.compile(r'aria-label="Job locations" data-bs-title="(.*?)">', re.S)
BI_POSTED = re.compile(r'fa-clock[^>]*></i>([^<]*)</span>')
BI_BLURB = re.compile(
    r'<div class="fs-sm fw-regular mb-md text-gray-04">(.*?)</div>', re.S)


# Built In writes countries as ISO-3 codes — "Berlin, DEU", "Amsterdam, NLD" —
# which us_status() cannot read, since it looks for prose country names. Left
# to it, every European posting here grades "unknown" and survives the US gate.
BI_ISO3 = re.compile(r"\b([A-Z]{3})\b")


def builtin_us(location):
    """Grade a Built In location, reading its ISO-3 country codes first."""
    codes = set(BI_ISO3.findall(location))
    if codes:
        # A posting open to several countries still qualifies if one is ours.
        return "us" if "USA" in codes else "no"
    return us_status(location)


def crawl_builtin(cfg, ctx):
    """Search the remote engineering board, one keyword at a time.

    The /jobs/remote/ path is not a remote guarantee — cards inside it still
    come back tagged "Hybrid" or "In-Office" — so the workplace tag on each
    card is what decides.
    """
    jobs, seen = [], set()
    for query in cfg.keywords:
        found = 0
        for page in range(1, cfg.pages + 1):
            url = BUILTIN_URL + "?" + urllib.parse.urlencode(
                {"search": query, "page": page})
            cards = ctx.fetch.get(url).split(BI_CARD)[1:]
            if not cards:
                break
            for raw in cards:
                card = re.sub(r"\s+", " ", raw)
                alias = BI_ALIAS.search(card)
                title = BI_TITLE.search(card)
                if not (alias and title):
                    continue
                link = "https://builtin.com" + alias.group(1)
                if link in seen:
                    continue
                seen.add(link)

                multi = BI_LOCATIONS.search(card)
                if multi:
                    location = ", ".join(
                        p for p in strip_tags(unescape(multi.group(1))).split("\n")
                        if p.strip())
                else:
                    m = BI_LOCATION.search(card)
                    location = strip_tags(m.group(1)) if m else ""
                workplace = BI_WORKPLACE.search(card)
                workplace = strip_tags(workplace.group(1)) if workplace else ""
                company = BI_COMPANY.search(card)
                posted = BI_POSTED.search(card)
                blurb = BI_BLURB.search(card)

                jobs.append(row(
                    "builtin", strip_tags(title.group(1)),
                    strip_tags(company.group(1)) if company else "",
                    (workplace + " " + location).strip(), link,
                    relative_date(strip_tags(posted.group(1)) if posted else ""),
                    remote=bool(REMOTE_HINT.search(workplace)),
                    us=builtin_us(location),
                    description=strip_tags(blurb.group(1)) if blurb else "",
                    query=query,
                ))
                found += 1
        ctx.report.source("builtin", f'"{query}": {found} postings')
    ctx.report.detail(f"{sum(1 for j in jobs if j['remote'])} of {len(jobs)} are remote")
    return jobs
