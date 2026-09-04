"""Ashby company boards, via the public posting API.

One request per company: the posting API returns whole postings, so there is
no detail pass. Each record already carries its description, an isRemote flag
and a per-country location list, which is the cleanest US signal any source
here offers.
"""

from ...filters.geo import US_COUNTRY, us_status
from ...filters.workplace import REMOTE_HINT
from ...models import row
from ...parse.html import strip_tags
from .driver import BoardSpec, make_source

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


def _posting(j, token, data):
    label, countries = ashby_places(j)
    if countries:
        status = "us" if countries & US_COUNTRY else "no"
    else:
        status = us_status(label)
    return row(
        "ashby", j.get("title", ""), token.title(), label or "Unspecified",
        j.get("jobUrl", ""), (j.get("publishedAt") or "")[:10],
        # workplaceType can read "Hybrid" while a "Remote (US)" alternate
        # location exists, so isRemote is the flag to trust.
        remote=bool(j.get("isRemote")) or bool(REMOTE_HINT.search(label)),
        us=status, description=strip_tags(j.get("descriptionHtml", "")),
        apply_url=j.get("applyUrl", ""),
    )


ASHBY = BoardSpec(
    name="ashby",
    boards=tuple(ASHBY_BOARDS),
    list_url=ASHBY_LIST,
    jobs_of=lambda d: (d or {}).get("jobs") or [],
    title_of=lambda j: j.get("title", ""),
    to_posting=_posting,
    skip=lambda j: j.get("isListed") is False,
)

crawl_ashby = make_source(ASHBY)
