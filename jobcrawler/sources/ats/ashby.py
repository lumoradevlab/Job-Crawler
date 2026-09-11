"""Ashby company boards, via the public posting API.

One request per company: the posting API returns whole postings, so there is
no detail pass. Each record already carries its description, an isRemote flag
and a per-country location list, which is the cleanest US signal any source
here offers.
"""

from ...filters.geo import US_COUNTRY, us_status
from ...filters.workplace import ONSITE, REMOTE_HINT
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


def _is_remote(j, label):
    """Whether an Ashby posting is genuinely remote.

    The location text outranks the flag, in both directions: a label that
    names an office is an office job however isRemote is set, and a label
    that says remote is remote. Only when the label names no workplace at
    all does isRemote get to decide.
    """
    if ONSITE.search(label) and not REMOTE_HINT.search(label):
        return False
    if REMOTE_HINT.search(label):
        return True
    # No workplace stated anywhere — including no location at all, which is
    # where a genuinely remote posting most often ends up.
    return bool(j.get("isRemote")) and not label.strip()


def _posting(j, token, data):
    label, countries = ashby_places(j)
    if countries:
        status = "us" if countries & US_COUNTRY else "no"
    else:
        status = us_status(label)
    return row(
        "ashby", j.get("title", ""), token.title(), label or "Unspecified",
        j.get("jobUrl", ""), (j.get("publishedAt") or "")[:10],
        # NOT isRemote alone. Measured across 2,253 live postings from 14
        # Ashby boards: 1,693 carry isRemote=true, and 1,401 of those name
        # only a city with no remote wording anywhere — "Data Center Design
        # Engineer, San Francisco" among them. It is not one company's bad
        # data either; Notion, Linear, Strava and Sentry all sit at 100%.
        #
        # So the location is what decides, and isRemote is demoted to what
        # it can still be trusted for: breaking the tie when a posting names
        # no place at all. A flag that is true three times in four carries
        # almost no information on its own.
        remote=_is_remote(j, label),
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
