"""Workable company boards, via the public widget API."""

import concurrent.futures as futures

from ...filters.geo import US_COUNTRY, us_status
from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT
from ...models import row
from ...parse.html import strip_tags
from .boards import board_list


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


def crawl_workable(cfg, ctx):
    boards = cfg.override_for("workable") or board_list("workable", WORKABLE_BOARDS, ctx)
    print(f"[workable] listing {len(boards)} company boards")

    def board(token):
        data = ctx.fetch.get_json(WORKABLE_LIST.format(token), tries=2) or {}
        company = data.get("name") or token.title()
        out = []
        for j in data.get("jobs") or []:
            title = (j.get("title") or "").strip()
            if not relevant(title, cfg.filters, "workable"):
                continue
            city = j.get("city") or ""
            country = j.get("country") or ""
            label = ", ".join(x for x in (city, j.get("state") or "", country) if x)
            if j.get("telecommuting"):
                label = (label + " (Remote)").strip()
            body = strip_tags(j.get("description") or "")
            # The country field is authoritative here, the way Ashby's is.
            if country:
                status = "us" if country.strip().lower() in US_COUNTRY else "no"
            else:
                status = us_status(label)
            out.append(row(
                "workable", title, company, label or "Unspecified",
                j.get("url") or j.get("shortlink", ""),
                (j.get("published_on") or j.get("created_at") or "")[:10],
                remote=bool(j.get("telecommuting"))
                or bool(REMOTE_HINT.search(label)),
                us=status, description=body,
            ))
        return out

    listed = []
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)
    print(f"  {len(listed)} Android/mobile titles, "
          f"{sum(1 for j in listed if j['remote'])} of them remote")
    return listed
