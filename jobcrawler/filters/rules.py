"""The gate every posting passes, whatever source it came from."""

import re
from datetime import datetime, timedelta

from ..roles.profiles import NOT_TECHNICAL, ROLE_WORDS
from .geo import us_status
from .workplace import HYBRID_SPLIT, ONSITE, REMOTE_HINT


# The Android gate, kept as the module default so a caller that never
# mentions a role behaves exactly as it always has. A profile overrides it
# per run via FilterConfig.subject — see jobcrawler/roles/.
RELEVANT = re.compile(
    r"\b(android|kotlin|jetpack\s*compose|mobile|react\s*native|flutter|"
    r"ios\s*/?\s*android)\b", re.I
)
ROLE = re.compile(r"\b(" + ROLE_WORDS + r")\b", re.I)


def _subject_pattern(filters):
    """The discipline regex this run is gating on.

    A run carries its profile as a compiled pattern on the filters; anything
    without one — the test suite's argparse Namespaces, a caller predating
    profiles — falls back to the Android gate that was here before.
    """
    return getattr(filters, "subject", None) or RELEVANT


def _role_pattern(filters):
    """The role-noun regex, which a profile may widen.

    "Manager" and "designer" are role nouns the engineering profiles must
    not accept — "Product Manager, Mobile" is not a mobile engineering job —
    so product and design carry their own additions rather than the shared
    list growing to fit them.
    """
    return getattr(filters, "role", None) or ROLE


def _is_technical(title):
    """False for the roles a discipline regex catches but nobody wants.

    "Sales Engineer" matches every sensible definition of engineer, and
    "Technical Recruiter" matches "technical". Both are jobs at a software
    company rather than software jobs, and on the wider profiles they are
    the single biggest source of noise.
    """
    return not NOT_TECHNICAL.search(title or "")

# Most sources apply the title gate themselves, before paying for a detail
# fetch — so a non-mobile title never reaches keep() and --why would report
# nothing about the rule that rejects the most postings by far. Routing all
# of them through one helper keeps that visible.
def relevant(title, filters, source="?", report=None):
    """The Android/mobile title gate the sources apply for themselves.

    The source names itself rather than the gate reading a "who is running
    now" field off shared state: the ATS crawlers call this from a six-worker
    pool, where one mutable field would attribute drops to whichever source
    happened to set it last.
    """
    if filters.no_filter or (_subject_pattern(filters).search(title)
                             and _role_pattern(filters).search(title)
                             and _is_technical(title)):
        return True
    if filters.why and report is not None:
        report.skipped(source, title)
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
    title = job.title
    # Structured boards put the role in the title; free-text sources (HN)
    # bury it in the body, so they set match_text to widen the gate.
    subject = job.ref.get("match_text") or title

    if not filters.no_filter:
        pattern = _subject_pattern(filters)
        if not (pattern.search(subject)
                and _role_pattern(filters).search(subject)):
            return "off-role: the title names no matching role"
        if not _is_technical(title):
            return "not-technical: a sales/recruiting/support title"
    if filters.must:
        hay = (title + " " + job.description).lower()
        absent = [w for w in filters.must if w.lower() not in hay]
        if absent:
            return "must: never says " + ", ".join(absent)
    if filters.exclude:
        banned = [w for w in filters.exclude if w.lower() in title.lower()]
        if banned:
            return "exclude: title says " + ", ".join(banned)
    if filters.easy_apply_only and job.easy_apply != "yes":
        return "easy-apply: not an Easy Apply posting"
    if not job.remote:
        return "not-remote: the source never flagged it remote"
    # A title that names the workplace outranks whatever the board's own
    # remote filter claimed — unless it offers both ("Remote or Hybrid").
    where = title + " " + job.location
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
        status = job.us or us_status(job.location)
        if status == "no":
            return "region: fenced outside the US (%s)" % (
                job.location or "no location given",)
        if filters.strict_us and status != "us":
            return "not-us: --strict-us, and the location reads %s" % status

    if filters.days and job.posted:
        try:
            posted = datetime.strptime(job.posted[:10], "%Y-%m-%d")
            if posted < datetime.now() - timedelta(days=filters.days):
                return "too-old: posted %s, window is %d days" % (
                    job.posted[:10], filters.days)
        except ValueError:
            pass
    return None
