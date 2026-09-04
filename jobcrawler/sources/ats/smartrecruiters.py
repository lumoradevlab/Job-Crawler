"""SmartRecruiters company boards, via the public postings API.

Listing pages carry the location but not the body, so — as with Greenhouse —
only the Android/mobile hits pay for a second request. It is also the one ATS
here whose listing is paged.
"""

from ...filters.geo import us_status
from ...filters.workplace import REMOTE_HINT, REMOTE_STRONG
from ...models import row
from ...parse.html import strip_tags
from .driver import BoardSpec, make_source

# Same silent-miss problem as Workable: an unknown company answers 200 with
# {"totalFound": 0} rather than 404, so a wrong slug looks like a quiet week.
SMARTRECRUITERS_BOARDS = "canva thales".split()

SR_LIST = ("https://api.smartrecruiters.com/v1/companies/{}/postings"
           "?limit=100&offset={}")
SR_JOB = "https://api.smartrecruiters.com/v1/companies/{}/postings/{}"
SR_PROBE = "https://api.smartrecruiters.com/v1/companies/{}/postings?limit=1"


def _posting(j, token, data):
    loc = j.get("location") or {}
    label = ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                  loc.get("country")) if x)
    if loc.get("remote"):
        label = (label + " (Remote)").strip()
    country = (loc.get("country") or "").strip().lower()
    return row(
        "smartrecruiters", j.get("name") or "", token.title(),
        label or "Unspecified",
        "https://jobs.smartrecruiters.com/{}/{}".format(token, j.get("id", "")),
        (j.get("releasedDate") or "")[:10],
        remote=bool(loc.get("remote")) or bool(REMOTE_HINT.search(label)),
        us=("us" if country in ("us", "united states")
            else ("no" if country else us_status(label))),
        sr_token=token, sr_id=j.get("id"),
    )


def _merge(j, data):
    ad = (data.get("jobAd") or {}).get("sections") or {}
    body = " ".join(
        strip_tags((ad.get(k) or {}).get("text") or "")
        for k in ("jobDescription", "qualifications", "companyDescription"))
    j["description"] = body[:2000]
    if not j["remote"]:
        j["remote"] = bool(REMOTE_STRONG.search(body))


SMARTRECRUITERS = BoardSpec(
    name="smartrecruiters",
    boards=tuple(SMARTRECRUITERS_BOARDS),
    list_url=SR_LIST,
    jobs_of=lambda d: (d or {}).get("content") or [],
    title_of=lambda j: j.get("name") or "",
    to_posting=_posting,
    detail_url=lambda j: SR_JOB.format(j["sr_token"], j["sr_id"]),
    merge_detail=_merge,
    ref_keys=("sr_token", "sr_id"),
    probe_url=SR_PROBE,
    paged=True,
)

crawl_smartrecruiters = make_source(SMARTRECRUITERS)
