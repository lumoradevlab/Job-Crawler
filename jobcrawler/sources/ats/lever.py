"""Lever company boards, via the public v0 posting API.

One request per company; each posting arrives whole. Lever states the
workplace outright in categories.workplaceType on modern boards and buries it
in the location label on older ones, so both are read before falling back to
the description.
"""

from datetime import datetime, timezone

from ...filters.geo import us_status
from ...filters.workplace import REMOTE_HINT, REMOTE_STRONG
from ...models import row
from ...parse.html import strip_tags
from .driver import BoardSpec, make_source

# Verified live: each token below answers the public v0 posting API with jobs.
# The list is deliberately short — Lever has no company index, so a slug is
# only discoverable by knowing the company uses Lever. `--discover` grows it.
LEVER_BOARDS = """
gopuff shieldai zoox ro anchorage cloudinary rigetti ledger
""".split()

LEVER_LIST = "https://api.lever.co/v0/postings/{}?mode=json"


def _posting(j, token, data):
    cats = j.get("categories") or {}
    label = cats.get("location") or ""
    # allLocations carries the remote alternates a single location field
    # hides — the same trap Ashby's secondaryLocations sets.
    extra = [x for x in (j.get("allLocations") or []) if x != label]
    if extra:
        label = " / ".join([label] + extra) if label else " / ".join(extra)
    workplace = (cats.get("workplaceType") or "").lower()
    body = strip_tags(j.get("descriptionPlain") or j.get("description", ""))
    ts = j.get("createdAt")
    posted = (datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")
              if isinstance(ts, (int, float)) else "")
    return row(
        "lever", j.get("text") or "", token.title(), label or "Unspecified",
        j.get("hostedUrl", ""), posted,
        remote=(workplace == "remote")
        or bool(REMOTE_HINT.search(label))
        or (not workplace and bool(REMOTE_STRONG.search(body))),
        us=us_status(label) if label else None,
        description=body, apply_url=j.get("applyUrl", ""),
    )


LEVER = BoardSpec(
    name="lever",
    boards=tuple(LEVER_BOARDS),
    list_url=LEVER_LIST,
    # Lever answers with a bare list, not an object wrapping one.
    jobs_of=lambda d: d if isinstance(d, list) else [],
    title_of=lambda j: j.get("text") or "",
    to_posting=_posting,
)

crawl_lever = make_source(LEVER)
