"""The gate every posting passes, whatever source it came from."""

import re
from datetime import datetime, timedelta

from .geo import us_status
from .workplace import HYBRID_SPLIT, ONSITE, REMOTE_HINT


RELEVANT = re.compile(
    r"\b(android|kotlin|jetpack\s*compose|mobile|react\s*native|flutter|"
    r"ios\s*/?\s*android)\b", re.I
)
ROLE = re.compile(
    r"\b(developer|engineer|engineering|programmer|swe|architect|"
    r"development)\b", re.I
)

# Most sources apply the title gate themselves, before paying for a detail
# fetch — so a non-mobile title never reaches keep() and --why would report
# nothing about the rule that rejects the most postings by far. Routing all
# of them through one helper keeps that visible. list.append is atomic under
# the GIL, so the crawlers' worker threads can write here without a lock.
_SKIPPED = []


def relevant(title, filters, source="?"):
    """The Android/mobile title gate the sources apply for themselves.

    The source names itself rather than the gate reading a "who is running
    now" field off shared state: the ATS crawlers call this from a six-worker
    pool, where one mutable field would attribute drops to whichever source
    happened to set it last.
    """
    if filters.no_filter or (RELEVANT.search(title) and ROLE.search(title)):
        return True
    if filters.why:
        _SKIPPED.append((source, title))
    return False


def keep(job, filters):
    """The single gate every posting must pass, whatever its source."""
    return rejection(job, filters) is None


def rejection(job, filters):
    """Which rule rejects this posting, or None if it passes them all.

    keep() is this function's yes/no shadow, so --why can name the rule that
    actually fired instead of a second copy of the rules drifting alongside
    the real ones. Every reason reads "<category>: <detail>"; the category is
    what the summary groups on, so keep those stable and the detail specific.
    """
    title = job["title"]
    # Structured boards put the role in the title; free-text sources (HN)
    # bury it in the body, so they set match_text to widen the gate.
    subject = job.get("match_text") or title

    if not filters.no_filter and not (RELEVANT.search(subject)
                                   and ROLE.search(subject)):
        return "not-mobile: no Android/mobile role in the title"
    if filters.must:
        hay = (title + " " + job.get("description", "")).lower()
        absent = [w for w in filters.must if w.lower() not in hay]
        if absent:
            return "must: never says " + ", ".join(absent)
    if filters.exclude:
        banned = [w for w in filters.exclude if w.lower() in title.lower()]
        if banned:
            return "exclude: title says " + ", ".join(banned)
    if filters.easy_apply_only and job.get("easy_apply") != "yes":
        return "easy-apply: not an Easy Apply posting"
    if not job.get("remote"):
        return "not-remote: the source never flagged it remote"
    # A title that names the workplace outranks whatever the board's own
    # remote filter claimed — unless it offers both ("Remote or Hybrid").
    where = title + " " + job.get("location", "")
    split = HYBRID_SPLIT.search(where)
    if split:
        return "hybrid-split: %r is a week split with an office" % (
            split.group(0).strip(),)
    # Read the workplace across title and location together. Tightening this
    # to the title alone looks right and is not: LinkedIn locations never say
    # "remote" (so the title is already the only signal there), while Built In
    # prepends its workplace tag to the place — "In-Office or Remote Dallas,
    # TX" — and that tag is the board stating the role genuinely offers both.
    office = ONSITE.search(where)
    if office and not REMOTE_HINT.search(where):
        return "onsite: says %r and never says remote" % (
            office.group(0).strip(),)

    if not filters.anywhere:
        status = job.get("us") or us_status(job.get("location", ""))
        if status == "no":
            return "region: fenced outside the US (%s)" % (
                job.get("location") or "no location given",)
        if filters.strict_us and status != "us":
            return "not-us: --strict-us, and the location reads %s" % status

    if filters.days and job.get("posted"):
        try:
            posted = datetime.strptime(job["posted"][:10], "%Y-%m-%d")
            if posted < datetime.now() - timedelta(days=filters.days):
                return "too-old: posted %s, window is %d days" % (
                    job["posted"][:10], filters.days)
        except ValueError:
            pass
    return None
