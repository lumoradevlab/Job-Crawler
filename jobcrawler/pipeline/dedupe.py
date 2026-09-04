"""Collapsing the same job carried by two sources onto its best link."""

import re
from html import unescape

from ..parse.text import COMPANY_NOISE


# Two sources rarely spell one job the same way. What varies between them is
# punctuation and a trailing workplace or location — "Mobile Engineer II
# (Android)" against "Mobile Engineer II, Android", "Reddit, Inc." against
# "Reddit" — so those are normalised away before the two are compared.
#
# Deliberately NOT normalised: seniority ("Senior Android Engineer" is not
# "Android Engineer"), a team or product in brackets ("(Payments)" is a
# different job), and employment type (a contract post and a permanent one
# are two openings). Dedupe that merges too much loses postings silently,
# which is worse than reporting one twice.
TITLE_SUFFIX = re.compile(
    r"[\s,]*[\(\[\-–—|]*\s*"
    r"\b(remote(\s*[-,(]?\s*(us|usa|united\s+states|anywhere))?|"
    r"us|usa|united\s+states|hybrid|on-?site|in-?office|"
    r"[mwfdxhn](\s*/\s*[mwfdxhn])+)\b"       # (m/f/d), (w/m/x) …
    r"\s*[\)\]]*\s*$", re.I)


def dedupe_key(job):
    """(title, company) reduced to what two sources would agree on."""
    title = unescape(job.get("title") or "").strip()
    for _ in range(3):                    # "Android Engineer (Remote) - US"
        shorter = TITLE_SUFFIX.sub("", title).strip()
        if shorter == title or not shorter:
            break
        title = shorter
    company = COMPANY_NOISE.sub(" ", unescape(job.get("company") or "").lower())
    flat = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    # Falling back to the raw field keeps a company literally called "Group"
    # from colliding with every other one that normalises to nothing.
    return (flat(title) or (job.get("title") or "").lower().strip(),
            flat(company) or (job.get("company") or "").lower().strip())


# Which link survives when two sources carry the same job. A company's own
# ATS is authoritative about location and stays live after the aggregators
# have rotated the posting out; an aggregator's redirect is the worst link to
# keep, so it ranks last.
SOURCE_RANK = {
    "greenhouse": 1, "ashby": 1, "lever": 1, "workable": 1,
    "smartrecruiters": 1, "usajobs": 2, "himalayas": 3, "wwr": 4,
    "builtin": 5, "arc": 5, "remoteok": 5, "arbeitnow": 5, "remotive": 5,
    "hn": 6, "linkedin": 7, "adzuna": 8,
    # Google Jobs links to whichever board Google indexed, which on a live
    # sample was bebee, lensa and jobmesh as often as Monster or LinkedIn —
    # so its link loses to a source that owns the posting it points at.
    "serpapi": 8, "jooble": 9,
}
