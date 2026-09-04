"""The one record every source normalises into.

This used to be a plain dict built by row(), which had no declared shape: a
source could add a key and nothing would notice until it turned up in the CSV,
and the per-source scratch a crawler needed between its two passes — Greenhouse
board tokens, LinkedIn job ids, the body HN matches its title gate against —
sat in the same namespace as the fields meant for output. Keeping the two
apart was a hand-written tuple of key names, BOOKKEEPING, popped before
writing; a list like that goes stale the first time someone adds a source and
forgets to extend it, and the failure mode is a stray column in a user's CSV.

So the fields are declared, and scratch goes in `ref`, which is never written
out. Nothing to remember to strip, and nothing to forget.
"""

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional

# Fields carried for bookkeeping only; asdict() would include ref otherwise.
_INTERNAL = ("ref",)


@dataclass
class Posting:
    """One job, however the board that carried it spelled things."""

    source: str
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    posted: str = ""
    remote: bool = True
    us: Optional[str] = None
    description: str = ""

    easy_apply: str = "?"
    apply_url: str = ""
    query: str = ""

    # Only a few sources state pay; the rest leave these empty.
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = ""
    salary_predicted: str = ""

    # Stamped by the run, not by the source.
    first_seen: str = ""

    # Per-source scratch: board tokens, job ids, the free text HN matches its
    # title gate against. Never written to any output file.
    ref: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Sources hand over whatever the board gave them, which is often None
        # where a string is expected.
        for name in ("source", "title", "company", "location", "url",
                     "posted", "description", "apply_url", "query",
                     "salary_currency", "salary_predicted", "first_seen"):
            value = getattr(self, name)
            setattr(self, name, (value or "").strip()
                    if name in ("title", "company", "location", "source")
                    else (value or ""))
        self.description = self.description[:2000]

    def as_record(self):
        """The posting as a plain dict, scratch excluded — what gets written."""
        return {k: v for k, v in asdict(self).items() if k not in _INTERNAL}

    @classmethod
    def from_record(cls, data):
        """Rebuild a posting from a written record, e.g. an archive line."""
        known = {f.name for f in fields(cls)} - set(_INTERNAL)
        return cls(**{k: v for k, v in data.items() if k in known})


# Every declared field, in the order they are written out.
RECORD_FIELDS = tuple(f.name for f in fields(Posting) if f.name not in _INTERNAL)


def row(source, title, company, location, url, posted="", remote=True,
        us=None, description="", match_text=None, **extra):
    """Build a Posting. Kept as a function so the sources read unchanged.

    Any keyword that isn't a declared field is scratch and goes to `ref` —
    which is how a source adds the working state it needs without that state
    being able to reach an output file.
    """
    declared = {f.name for f in fields(Posting)}
    ref = dict(extra.pop("ref", None) or {})
    if match_text is not None:
        ref["match_text"] = match_text
    for key in list(extra):
        if key not in declared:
            ref[key] = extra.pop(key)
    return Posting(source=source, title=title, company=company,
                   location=location, url=url, posted=posted, remote=remote,
                   us=us, description=description, ref=ref, **extra)
