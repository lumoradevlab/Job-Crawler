"""Lever company boards, via the public v0 posting API."""

import concurrent.futures as futures
from datetime import datetime, timezone

from ...filters.geo import us_status
from ...filters.rules import relevant
from ...filters.workplace import REMOTE_HINT, REMOTE_STRONG
from ...models import row
from ...parse.html import strip_tags
from .boards import board_list


# Verified live: each token below answers the public v0 posting API with jobs.
# The list is deliberately short — Lever has no company index, so a slug is
# only discoverable by knowing the company uses Lever. `--discover` grows it.
LEVER_BOARDS = """
gopuff shieldai zoox ro anchorage cloudinary rigetti ledger
""".split()

LEVER_LIST = "https://api.lever.co/v0/postings/{}?mode=json"


def crawl_lever(cfg, ctx):
    """One request per company; each posting arrives whole.

    Lever states the workplace outright in categories.commitment/workplaceType
    on modern boards, and buries it in the location label on older ones, so
    both are read before falling back to the description.
    """
    boards = cfg.override_for("lever") or board_list("lever", LEVER_BOARDS, ctx)
    print(f"[lever] listing {len(boards)} company boards")

    def board(token):
        data = ctx.fetch.get_json(LEVER_LIST.format(token), tries=2)
        if not isinstance(data, list):
            return []
        out = []
        for j in data:
            title = (j.get("text") or "").strip()
            if not relevant(title, cfg.filters, "lever"):
                continue
            cats = j.get("categories") or {}
            label = cats.get("location") or ""
            # allLocations carries the remote alternates a single location
            # field hides — the same trap Ashby's secondaryLocations sets.
            extra = [x for x in (j.get("allLocations") or []) if x != label]
            if extra:
                label = " / ".join([label] + extra) if label else " / ".join(extra)
            workplace = (cats.get("workplaceType") or "").lower()
            body = strip_tags(j.get("descriptionPlain")
                              or j.get("description", ""))
            ts = j.get("createdAt")
            posted = (datetime.fromtimestamp(ts / 1000, timezone.utc)
                      .strftime("%Y-%m-%d")
                      if isinstance(ts, (int, float)) else "")
            out.append(row(
                "lever", title, token.title(), label or "Unspecified",
                j.get("hostedUrl", ""), posted,
                remote=(workplace == "remote")
                or bool(REMOTE_HINT.search(label))
                or (not workplace and bool(REMOTE_STRONG.search(body))),
                us=us_status(label) if label else None,
                description=body, apply_url=j.get("applyUrl", ""),
            ))
        return out

    listed = []
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(board, boards):
            listed.extend(res)
    print(f"  {len(listed)} Android/mobile titles, "
          f"{sum(1 for j in listed if j['remote'])} of them remote")
    return listed
