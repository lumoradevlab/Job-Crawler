"""Greenhouse company boards, via the public job-board API."""

from ...filters.geo import us_status
from ...filters.workplace import REMOTE_HINT, REMOTE_STRONG
from ...models import row
from ...parse.html import strip_tags
from .driver import BoardSpec, make_source

# Verified live: every token below returns jobs from the public board API.
GREENHOUSE_BOARDS = """
databricks stripe anthropic waymo lucidmotors brex braze roblox pinterest
samsara scaleai affirm airbnb lyft coinbase figma epicgames klaviyo reddit
asana robinhood instacart gusto duolingo faire twitch mercury sofi carta
chime mixpanel peloton discord attentive dropbox betterment amplitude
life360 webflow coursera squarespace udemy masterclass medium
""".split()

GH_LIST = "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=false"
GH_JOB = "https://boards-api.greenhouse.io/v1/boards/{}/jobs/{}"


def _posting(j, token, data):
    loc = (j.get("location") or {}).get("name", "")
    return row(
        "greenhouse", j.get("title", ""),
        j.get("company_name") or token.title(), loc,
        j.get("absolute_url", ""),
        (j.get("updated_at") or j.get("first_published") or "")[:10],
        remote=bool(REMOTE_HINT.search(loc)),
        gh_token=token, gh_id=j.get("id"),
    )


def _merge(j, data):
    """A location of "San Francisco, CA" doesn't mean the role isn't
    remote-eligible — Greenhouse keeps that detail in the body, which is why
    the matches pay for a second request at all."""
    body = strip_tags(data.get("content", ""))
    j.description = body[:2000]
    # The location field is the reliable signal; the body only counts if
    # it commits to remote in so many words.
    j.remote = bool(REMOTE_HINT.search(j.location)) or \
        bool(REMOTE_STRONG.search(body))
    located = us_status(j.location)
    j.us = located if located != "unknown" else us_status(body[:1500])


GREENHOUSE = BoardSpec(
    name="greenhouse",
    boards=tuple(GREENHOUSE_BOARDS),
    list_url=GH_LIST,
    jobs_of=lambda d: (d or {}).get("jobs") or [],
    title_of=lambda j: j.get("title", ""),
    to_posting=_posting,
    detail_url=lambda j: GH_JOB.format(j.ref["gh_token"], j.ref["gh_id"]),
    merge_detail=_merge,
)

crawl_greenhouse = make_source(GREENHOUSE)
