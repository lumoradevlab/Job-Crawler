"""Hacker News "Ask HN: Who is hiring?", via the official HN API."""

import concurrent.futures as futures
import re
import time
from datetime import datetime, timezone

from ...filters.geo import us_status
from ...filters.rules import RELEVANT
from ...filters.workplace import REMOTE_HINT
from ...models import row
from ...net.http import fetch_json
from ...parse.html import strip_tags


HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"


def crawl_hn(args):
    user = fetch_json("https://hacker-news.firebaseio.com/v0/user/whoishiring.json")
    if not user:
        print("[hn] could not read the whoishiring user")
        return []

    thread = None
    for sid in (user.get("submitted") or [])[:12]:
        item = fetch_json(HN_ITEM.format(sid)) or {}
        if re.search(r"who is hiring", item.get("title", ""), re.I):
            thread = item
            break
    if not thread:
        print("[hn] no 'Who is hiring?' thread found")
        return []

    kids = thread.get("kids", [])
    print(f"[hn] {thread.get('title')} — {len(kids)} top-level posts")

    def one(cid):
        c = fetch_json(HN_ITEM.format(cid), tries=2) or {}
        if not c or c.get("deleted") or c.get("dead") or not c.get("text"):
            return None
        body = strip_tags(c["text"])
        if not RELEVANT.search(body):
            return None
        headline = body.split("\n")[0][:150]
        company = re.split(r"\s*[|–-]\s*", headline)[0][:60]
        return row(
            "hn", headline, company, headline,
            f"https://news.ycombinator.com/item?id={c['id']}",
            datetime.fromtimestamp(c.get("time", 0), timezone.utc)
                .strftime("%Y-%m-%d") if c.get("time") else "",
            remote=bool(REMOTE_HINT.search(body)),
            us=us_status(body[:600]), description=body,
            # The role lives in the post body, not in a title field.
            match_text=body,
        )

    # The HN API refuses connections past ~4 concurrent clients, so this
    # stays deliberately modest and walks the thread in batches.
    jobs = []
    with futures.ThreadPoolExecutor(max_workers=4) as ex:
        for i in range(0, len(kids), 40):
            for res in ex.map(one, kids[i:i + 40]):
                if res:
                    jobs.append(res)
            time.sleep(1.0)
    print(f"  {len(jobs)} mention Android/mobile")
    return jobs
