"""Ashby company boards, via the public posting API."""

import concurrent.futures as futures

from ...filters.geo import US_COUNTRY, us_status
from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT
from ...models import row
from ...parse.html import strip_tags
from .boards import board_list


# Verified live: every token below answers the public posting API with jobs.
ASHBY_BOARDS = """
openai notion plaid ramp vanta linear strava sentry supabase cursor harvey
elevenlabs abridge headway sierra decagon watershed render posthog resend
substack patreon incident railway warp modal hex temporal applied mercor
browserbase openevidence neon unit mux
""".split()

ASHBY_LIST = "https://api.ashbyhq.com/posting-api/job-board/{}"


def ashby_places(job):
    """Every location on a posting: the primary one plus its alternates.

    Returns (label, countries). A posting headquartered in New York but open
    to "Remote (US)" carries that only in secondaryLocations, so reading the
    primary location alone would misjudge both the remote flag and the country.
    """
    places, countries = [], set()

    def take(loc, addr):
        if loc:
            places.append(loc)
        country = ((addr or {}).get("postalAddress") or {}).get("addressCountry")
        if country:
            countries.add(country.strip().lower())

    take(job.get("location"), job.get("address"))
    for sec in job.get("secondaryLocations") or []:
        take(sec.get("location"), sec.get("address"))
    return " / ".join(dict.fromkeys(places)), countries


def crawl_ashby(cfg, ctx):
    """One request per company — the posting API returns the full postings.

    Unlike Greenhouse there is no second fetch per job: each record already
    carries its description, an isRemote flag and a per-country location
    list, which is the cleanest US signal any source here offers.
    """
    boards = cfg.override_for("ashby") or board_list("ashby", ASHBY_BOARDS, ctx)
    print(f"[ashby] listing {len(boards)} company boards")

    def board(token):
        data = ctx.fetch.get_json(ASHBY_LIST.format(token), tries=2) or {}
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            title = j.get("title", "").strip()
            if not relevant(title, cfg.filters, "ashby"):
                continue
            label, countries = ashby_places(j)
            if countries:
                status = "us" if countries & US_COUNTRY else "no"
            else:
                status = us_status(label)
            body = strip_tags(j.get("descriptionHtml", ""))
            out.append(row(
                "ashby", title, token.title(), label or "Unspecified",
                j.get("jobUrl", ""), (j.get("publishedAt") or "")[:10],
                # workplaceType can read "Hybrid" while a "Remote (US)"
                # alternate location exists, so isRemote is the flag to trust.
                remote=bool(j.get("isRemote"))
                or bool(REMOTE_HINT.search(label)),
                us=status, description=body,
                apply_url=j.get("applyUrl", ""),
            ))
        return out

    listed = []
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)
    print(f"  {len(listed)} Android/mobile titles, "
          f"{sum(1 for j in listed if j['remote'])} of them remote")
    return listed
