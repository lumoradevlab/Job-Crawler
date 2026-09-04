"""What one run accumulates while it works.

The counterpart to CrawlConfig: everything here is discovered or built at run
time rather than typed at the command line. It used to be attached to the
argparse Namespace after parsing — `args.seen_keys`, `args.boards_found`,
`args.source_now` — which is how a settings object quietly became a place to
stash state, and why sources reached for it with getattr and a default in
case main() had not got round to setting it.

Handing a source a RunContext says what it may expect and guarantees it is
there, so `getattr(args, "seen_keys", set())` becomes `ctx.seen_keys`.

`source_now` is deliberately not here. It existed so a title dropped by a
source's own gate could be attributed in the --why report, but every source
already knows its own name and can say it — a mutable "who is running right
now" field shared across a thread pool is a race waiting to be written.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Set

from .net.http import Fetcher
from .report.events import Reporter


@dataclass
class RunContext:
    """The run's shared, mutable working state."""

    fetch: Fetcher = field(default_factory=Fetcher)

    # Where progress goes, and the ledger of titles a source turned away.
    report: Reporter = field(default_factory=Reporter)
    today: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    # Job keys already reported by an earlier run. Sources consult it to skip
    # detail fetches they have already paid for, which is why it reaches them
    # at all rather than being applied afterwards.
    seen_keys: Set[str] = field(default_factory=set)

    # ATS boards --discover has found on previous runs: {ats: [slug, ...]}.
    boards_found: Dict[str, list] = field(default_factory=dict)
