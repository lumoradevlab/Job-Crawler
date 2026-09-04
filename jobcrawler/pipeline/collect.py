"""Running the sources, and remembering which of them actually worked.

One source failing must not take the run down, so the loop swallows whatever
a crawler raises and carries on. But swallowing it silently is how a failure
turns into wrong data: the run went on to record that it had crawled up to
today, and the next morning catchup_days() narrowed the window to the day
since — so everything the broken source would have returned fell outside the
window and was never asked for again.

So the collector reports which sources succeeded, and only those get their
clock advanced. A source that has been failing for a week is asked for a
week's worth when it comes back.
"""


class Outcome:
    """What one pass over the sources produced."""

    def __init__(self, postings, succeeded, interrupted=False):
        self.postings = postings
        # Sources that ran to completion. Only these may advance their clock.
        self.succeeded = succeeded
        self.interrupted = interrupted


def collect(cfg, ctx, sources):
    """Crawl every requested source, isolating each one's failures."""
    postings, succeeded, interrupted = [], set(), False
    for name in cfg.sources:
        try:
            postings.extend(sources[name](cfg, ctx))
        except KeyboardInterrupt:
            ctx.report.result("\ninterrupted — writing what we have")
            interrupted = True
            break
        except Exception as e:              # keep the other sources alive
            ctx.report.warn(f"  ! {name} failed: {type(e).__name__}: {e}")
            continue
        succeeded.add(name)

    # A source can return cleanly and still have learned nothing: if every
    # request failed, its empty list is silence, not an answer. Advancing any
    # clock on that would lose the window just as surely as an exception.
    if ctx.fetch.stats.total_blackout():
        succeeded = set()

    return Outcome(postings, succeeded, interrupted)
