"""Boards that state an age instead of a date."""

import re
from datetime import datetime, timedelta


def relative_date(text, today=None):
    """Turn "Reposted 3 Days Ago" into a date.

    Built In posts no timestamps at all, and Google Jobs states its own as
    "3 days ago" / "22 hours ago", so both are read here.
    """
    today = today or datetime.now()
    t = text.replace("Reposted", "").strip().lower()
    if not t:
        return ""
    if t.startswith(("today", "just", "moments")) or "hour" in t or "minute" in t:
        return today.strftime("%Y-%m-%d")
    if t.startswith("yesterday"):
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.match(r"(\d+)\+?\s+days?\s+ago", t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    return ""
