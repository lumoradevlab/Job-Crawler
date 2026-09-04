"""Crawl remote Android/mobile developer jobs, US-only by default.

Every source is normalised into one record and graded through the same gates:
an Android/mobile title, a genuine remote flag, and us_status() — which drops
postings fenced to another region, keeps "Worldwide"/"Anywhere" ones (a US
applicant qualifies), and under --strict-us keeps only those naming the US.

Python 3 stdlib only. `jobcrawler --help` for the command line.

What follows is the package's public API: the pieces worth importing if you
are building on this rather than running it. A source can be driven directly
with nothing but a config and a context, neither of which involves argparse:

    from jobcrawler import CrawlConfig, RunContext, crawl_greenhouse

    postings = crawl_greenhouse(CrawlConfig(), RunContext())

Anything not listed here is an internal detail. Reaching past this list is
allowed — it is Python — but those names may move without notice.
"""

from .config import CrawlConfig, FilterConfig
from .context import RunContext
from .filters.geo import us_status
from .filters.rules import keep, rejection, relevant
from .models import RECORD_FIELDS, Posting, row
from .net.http import Failure, Fetcher, Stats
from .net.ratelimit import HostPolicy, RateLimiter
from .parse.dates import relative_date
from .parse.html import strip_tags
from .parse.salary import annualise, parse_salary
from .pipeline.collect import collect
from .pipeline.dedupe import SOURCE_RANK, dedupe_key
from .pipeline.select import select, split_new
from .report.events import NullReporter, Reporter
from .report.writers import COLUMNS, report_rejections, write_outputs
from .sources.registry import DEFAULT_SOURCES, SOURCES
from .store.archive import Archive
from .store.seen import catchup_days, job_key, record_run

__version__ = "0.1.0"

__all__ = [
    # what the user asked for, and what one run accumulates
    "CrawlConfig", "FilterConfig", "RunContext",
    # the record
    "Posting", "row", "RECORD_FIELDS",
    # the gate
    "keep", "rejection", "relevant", "us_status",
    # request layer
    "Fetcher", "Failure", "Stats", "RateLimiter", "HostPolicy",
    # parsing helpers worth reusing
    "strip_tags", "parse_salary", "annualise", "relative_date",
    # the run's stages
    "collect", "select", "split_new", "dedupe_key", "SOURCE_RANK",
    # sources
    "SOURCES", "DEFAULT_SOURCES",
    # what a run remembers, and what it writes
    "Archive", "job_key", "catchup_days", "record_run",
    "COLUMNS", "report_rejections", "write_outputs",
    "Reporter", "NullReporter",
    "__version__",
]
