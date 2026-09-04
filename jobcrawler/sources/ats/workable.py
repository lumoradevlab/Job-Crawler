"""Workable company boards, via the public widget API."""

from ...filters.geo import US_COUNTRY, us_status
from ...filters.workplace import REMOTE_HINT
from ...models import row
from ...parse.html import strip_tags
from .driver import BoardSpec, make_source

# The documented v3 path (apply.workable.com/api/v3/accounts/…) needs a bearer
# token; this widget endpoint is the public one and needs nothing.
#
# Caveat worth knowing: an unknown slug 404s, but a real account that isn't
# hiring answers 200 with its name and an empty job list — so a bad entry here
# is quiet, not silent, and the two are told apart by whether "name" comes
# back at all. Most of the list below is currently in that second state.
WORKABLE_BOARDS = """
bolt paddle cloudtalk veriff hotjar aircall personio pleo sumup mollie
productboard mews contentful staffbase
""".split()

WORKABLE_LIST = "https://apply.workable.com/api/v1/widget/accounts/{}"


def _posting(j, token, data):
    city = j.get("city") or ""
    country = j.get("country") or ""
    label = ", ".join(x for x in (city, j.get("state") or "", country) if x)
    if j.get("telecommuting"):
        label = (label + " (Remote)").strip()
    # The country field is authoritative here, the way Ashby's is.
    if country:
        status = "us" if country.strip().lower() in US_COUNTRY else "no"
    else:
        status = us_status(label)
    return row(
        "workable", j.get("title") or "",
        (data or {}).get("name") or token.title(),
        label or "Unspecified",
        j.get("url") or j.get("shortlink", ""),
        (j.get("published_on") or j.get("created_at") or "")[:10],
        remote=bool(j.get("telecommuting")) or bool(REMOTE_HINT.search(label)),
        us=status, description=strip_tags(j.get("description") or ""),
    )


WORKABLE = BoardSpec(
    name="workable",
    boards=tuple(WORKABLE_BOARDS),
    list_url=WORKABLE_LIST,
    jobs_of=lambda d: (d or {}).get("jobs") or [],
    title_of=lambda j: j.get("title") or "",
    to_posting=_posting,
)

crawl_workable = make_source(WORKABLE)
