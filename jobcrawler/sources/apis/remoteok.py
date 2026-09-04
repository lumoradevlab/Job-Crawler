"""RemoteOK's free public job API."""

from ...models import row
from ...parse.html import strip_tags


def crawl_remoteok(cfg, ctx):
    data = ctx.fetch.get_json("https://remoteok.com/api") or []
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is a legal notice
        out.append(row(
            "remoteok", j.get("position", ""), j.get("company", ""),
            j.get("location") or "Remote", j.get("url", ""),
            (j.get("date") or "")[:10], remote=True,
            description=strip_tags(j.get("description", "")),
        ))
    print(f"[remoteok] {len(out)} postings")
    return out
