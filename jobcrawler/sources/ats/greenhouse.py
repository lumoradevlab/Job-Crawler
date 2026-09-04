"""Greenhouse company boards, via the public job-board API."""

import concurrent.futures as futures

from ...filters.geo import us_status
from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT, REMOTE_STRONG
from ...models import row
from ...parse.html import strip_tags
from ...store.seen import job_key
from .boards import board_list


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


def crawl_greenhouse(cfg, ctx):
    """Two passes: cheap title listing, then full text for the hits only.

    A location of "San Francisco, CA" doesn't mean the role isn't
    remote-eligible — Greenhouse keeps that detail in the body, so only the
    handful of Android/mobile matches pay for a second request.
    """
    boards = cfg.override_for("greenhouse") or board_list("greenhouse", GREENHOUSE_BOARDS, ctx)
    print(f"[greenhouse] listing {len(boards)} company boards")
    listed = []

    def board(token):
        data = ctx.fetch.get_json(GH_LIST.format(token), tries=2) or {}
        out = []
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            out.append(row(
                "greenhouse", j.get("title", ""),
                j.get("company_name") or token.title(), loc,
                j.get("absolute_url", ""),
                (j.get("updated_at") or j.get("first_published") or "")[:10],
                remote=bool(REMOTE_HINT.search(loc)),
                gh_token=token, gh_id=j.get("id"),
            ))
        return out

    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)

    hits = [j for j in listed if relevant(j["title"], cfg.filters, "greenhouse")]
    print(f"  {len(listed)} postings scanned, {len(hits)} Android/mobile titles")

    # The board listing is one request per company, but each full posting is
    # its own request — so never re-read one already in the history.
    known = ctx.seen_keys
    hits = [j for j in hits if job_key(j) not in known]
    if known:
        print(f"  {len(hits)} of those are new; skipping the rest")

    def detail(j):
        data = ctx.fetch.get_json(GH_JOB.format(j["gh_token"], j["gh_id"]), tries=2)
        if not data:
            return j
        body = strip_tags(data.get("content", ""))
        j["description"] = body[:2000]
        # The location field is the reliable signal; the body only counts if
        # it commits to remote in so many words.
        j["remote"] = bool(REMOTE_HINT.search(j["location"])) or \
            bool(REMOTE_STRONG.search(body))
        located = us_status(j["location"])
        j["us"] = located if located != "unknown" else us_status(body[:1500])
        return j

    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        hits = list(ex.map(detail, hits))
    print(f"  {sum(1 for j in hits if j['remote'])} of them are remote")
    return hits
