"""Turning markup into text, and Next.js payloads into dicts."""

import json
import re
from html import unescape


def strip_tags(html):
    html = re.sub(r"<br\s*/?>", "\n", html)
    html = re.sub(r"</(p|li|ul|div)>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    # unescape twice: HN and some RSS feeds double-encode their entities
    html = unescape(unescape(html))
    html = re.sub(r"[ \t]+", " ", html)
    return re.sub(r"\n{3,}", "\n\n", html).strip()


def next_data(html):
    """Pull the pageProps object out of a Next.js page."""
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    except json.JSONDecodeError:
        return {}
