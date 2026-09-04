"""SmartRecruiters company boards, via the public postings API."""

import concurrent.futures as futures

from ...filters.geo import us_status
from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT, REMOTE_STRONG
from ...models import row
from ...parse.html import strip_tags
from ...store.seen import job_key
from .boards import board_list


# Same silent-miss problem as Workable: an unknown company answers 200 with
# {"totalFound": 0} rather than 404, so a wrong slug looks like a quiet week.
SMARTRECRUITERS_BOARDS = "canva thales".split()

SR_LIST = ("https://api.smartrecruiters.com/v1/companies/{}/postings"
           "?limit=100&offset={}")
SR_JOB = "https://api.smartrecruiters.com/v1/companies/{}/postings/{}"


def crawl_smartrecruiters(cfg, ctx):
    """Listing pages carry the location but not the body, so — as with
    Greenhouse — only the Android/mobile hits pay for a second request."""
    boards = cfg.override_for("smartrecruiters") or board_list("smartrecruiters", SMARTRECRUITERS_BOARDS, ctx)
    print(f"[smartrecruiters] listing {len(boards)} company boards")

    def board(token):
        out, offset = [], 0
        while offset < 400:               # a company with more than 400 open
            data = ctx.fetch.get_json(SR_LIST.format(token, offset), tries=2) or {}
            batch = data.get("content") or []
            if not batch:
                break
            for j in batch:
                title = (j.get("name") or "").strip()
                if not relevant(title, cfg.filters, "smartrecruiters"):
                    continue
                loc = j.get("location") or {}
                label = ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                              loc.get("country")) if x)
                if loc.get("remote"):
                    label = (label + " (Remote)").strip()
                country = (loc.get("country") or "").strip().lower()
                out.append(row(
                    "smartrecruiters", title, token.title(),
                    label or "Unspecified",
                    "https://jobs.smartrecruiters.com/{}/{}".format(
                        token, j.get("id", "")),
                    (j.get("releasedDate") or "")[:10],
                    remote=bool(loc.get("remote"))
                    or bool(REMOTE_HINT.search(label)),
                    us=("us" if country in ("us", "united states")
                        else ("no" if country else us_status(label))),
                    sr_token=token, sr_id=j.get("id"),
                ))
            if len(batch) < 100:
                break
            offset += 100
        return out

    listed = []
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)

    known = ctx.seen_keys
    hits = [j for j in listed if job_key(j) not in known]

    def detail(j):
        data = ctx.fetch.get_json(SR_JOB.format(j.pop("sr_token"), j.pop("sr_id")),
                          tries=2)
        if data:
            ad = (data.get("jobAd") or {}).get("sections") or {}
            body = " ".join(
                strip_tags((ad.get(k) or {}).get("text") or "")
                for k in ("jobDescription", "qualifications", "companyDescription"))
            j["description"] = body[:2000]
            if not j["remote"]:
                j["remote"] = bool(REMOTE_STRONG.search(body))
        return j

    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        hits = list(ex.map(detail, hits))
    for j in listed:                       # drop bookkeeping from skipped rows
        j.pop("sr_token", None), j.pop("sr_id", None)
    print(f"  {len(listed)} Android/mobile titles, "
          f"{sum(1 for j in hits if j['remote'])} of them remote")
    return hits
