"""Turning markup into text, and Next.js payloads into dicts."""

import json
import re
from html import unescape


# Boards do not agree on whether their body is markup or text about markup.
# Greenhouse escapes the whole thing — a description arrives as
# "&lt;h2&gt;Who we are&lt;/h2&gt;" — so stripping tags first finds none, and
# the unescape afterwards leaves literal <h2> in the output.
#
# Unescaping everything first fixes that and breaks something worse. Android
# job descriptions are full of generics, and "Flow&lt;List&lt;User&gt;&gt;"
# unescapes into something the tag regex eats whole, turning a sentence about
# Kotlin into "Flow >". So only escaped sequences that name an actual HTML tag
# are turned back into markup; "&lt;String&gt;" is left alone and survives to
# the final unescape as text.
_HTML_TAGS = (
    "p|br|hr|div|span|section|article|ul|ol|li|dl|dt|dd|h[1-6]|"
    "strong|b|em|i|u|s|small|sup|sub|font|"
    "a|img|table|thead|tbody|tfoot|tr|td|th|blockquote|code|pre"
)
ESCAPED_TAG = re.compile(
    # An attribute may itself contain an entity ("href=\"a&amp;b\""), so the
    # body of the tag allows any & that does not start the closing &gt;.
    r"&lt;\s*/?\s*(?:" + _HTML_TAGS + r")\b(?:[^&]|&(?!lt;|gt;))*?&gt;",
    re.I,
)


def strip_tags(html):
    html = ESCAPED_TAG.sub(lambda m: unescape(m.group(0)), html)
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
