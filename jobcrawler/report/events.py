"""Where a run says what it is doing.

Progress was 56 print() calls spread across every module, which had three
costs. There was no way to turn it off, so running the crawler from cron meant
redirecting stdout and losing the errors with it. There was no way to test a
source without capturing stdout. And the --why ledger of titles a source
turned away lived in a module-level list that the crawlers' worker threads
appended to, which worked only because list.append happens to be atomic under
the GIL — a property the code depended on and never chose.

A Reporter is passed in like the Fetcher is. Sources say what happened; the
reporter decides whether that reaches a terminal, and holds the ledger.
"""

import sys


class Reporter:
    """The interface a source may rely on. Prints to the terminal.

    Two shapes cover everything the crawl says: a headline naming the source,
    and an indented line under it. Anything that is a problem rather than
    progress goes to stderr, so redirecting stdout keeps the warnings visible.
    """

    def __init__(self, quiet=False, stream=None, errors=None):
        self.quiet = quiet
        # Left as None the streams are resolved per call, not captured here.
        # Binding sys.stdout at construction would make a Reporter built
        # before a redirect_stdout keep writing to the real terminal.
        self.stream = stream
        self.errors = errors
        # (source, title) for every posting a source turned away before the
        # main gate saw it. Held here rather than in a module global so a run
        # owns its own ledger and tests need not reset anything.
        self.skips = []

    # -- progress ----------------------------------------------------------
    def source(self, name, message):
        """A headline: what one source is starting or has finished."""
        self._out(f"[{name}] {message}")

    def detail(self, message):
        """An indented line under the current source's headline."""
        self._out(f"  {message}")

    def line(self, message=""):
        """An unindented line, for the run's own summary."""
        self._out(message)

    def result(self, message=""):
        """The answer the run was started for. Never suppressed.

        --quiet exists to drop progress from a cron log, not to make the tool
        silent; a run that printed nothing at all would be indistinguishable
        from one that never started.
        """
        print(message, file=self.stream or sys.stdout)

    def warn(self, message):
        """Something went wrong. Always shown, even when quiet."""
        print(message, file=self.errors or sys.stderr)

    def _out(self, text):
        if not self.quiet:
            print(text, file=self.stream or sys.stdout)

    # -- the --why ledger --------------------------------------------------
    def skipped(self, source, title):
        """Record a title dropped by a source's own gate.

        Called from the ATS worker pools, so it must stay append-only and
        cheap; a list append under CPython needs no lock, and unlike the old
        module global this one belongs to a single run.
        """
        self.skips.append((source, title))


class NullReporter(Reporter):
    """Says nothing at all, not even warnings. For tests."""

    def __init__(self):
        super().__init__(quiet=True)

    def result(self, message=""):
        """The answer the run was started for. Never suppressed.

        --quiet exists to drop progress from a cron log, not to make the tool
        silent; a run that printed nothing at all would be indistinguishable
        from one that never started.
        """
        print(message, file=self.stream or sys.stdout)

    def warn(self, message):
        pass
