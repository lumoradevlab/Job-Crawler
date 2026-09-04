"""What the user asked for, frozen once the CLI has been parsed.

This replaces reading settings off the argparse Namespace. The Namespace was
doing two jobs at once: carrying the flags the user typed, and carrying run
state that main() attached to it afterwards (`seen_keys`, `boards_found`,
`source_now`). Sources then dug that state back out with getattr and a
default, which meant every source depended on the shape of the CLI parser and
none of them could be called without building one.

Splitting the two is the whole point. Settings are frozen and live here; the
state a run accumulates lives in RunContext and is explicitly mutable. A
source that takes both can be called from a test with two small objects and
no argparse anywhere.

FilterConfig deliberately carries exactly the fields rejection() reads, and
no more. That keeps it duck-compatible with the argparse Namespace the test
suite builds, so the ~40 tests asserting on the grading rules go on passing
against the real function rather than a re-implementation of it.
"""

from dataclasses import dataclass, field, replace
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class FilterConfig:
    """Everything the keep/reject gate consults, and nothing else."""

    no_filter: bool = False
    must: Optional[Tuple[str, ...]] = None
    exclude: Optional[Tuple[str, ...]] = None
    easy_apply_only: bool = False
    anywhere: bool = False
    strict_us: bool = False
    days: int = 0
    why: bool = False
    min_salary: Optional[int] = None


@dataclass(frozen=True)
class CrawlConfig:
    """How wide to cast the net, and where."""

    keywords: Tuple[str, ...] = ()
    sources: Tuple[str, ...] = ()
    location: str = "United States"
    pages: int = 5
    days: int = 60
    level: Optional[str] = None
    delay: float = 4.0
    details: bool = False
    discover: bool = False
    # Per-ATS slug overrides from --boards/--ashby-boards/... An absent or
    # empty entry means "use the built-in list plus whatever --discover found",
    # which is why this is a lookup rather than five separate fields.
    boards: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    filters: FilterConfig = field(default_factory=FilterConfig)

    def override_for(self, ats):
        """The slugs the user pinned for one ATS, or () for "use the list"."""
        return tuple(self.boards.get(ats) or ())

    def with_days(self, days):
        """A copy narrowed to a catch-up window, gate included.

        The window is asked of the boards *and* applied by the date rule, so
        the two must never drift apart — hence one method that moves both.
        """
        return replace(self, days=days,
                       filters=replace(self.filters, days=days))
