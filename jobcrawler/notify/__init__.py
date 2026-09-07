"""Delivering a run's new postings somewhere other than a file.

The crawler already knows what is new: split_new() returns exactly the jobs
never reported before, which is the same question a notifier asks. So a bot
is a thin layer over a normal run rather than a second crawler — it runs the
pipeline, takes the new postings, and sends them on.

Nothing here is imported by the crawl itself. A notifier that fails must not
take the run with it: the postings are already written and archived by then,
and a Telegram outage is not a reason to lose a morning's crawl.
"""

from .telegram import TelegramError, TelegramNotifier, format_posting

__all__ = ["TelegramNotifier", "TelegramError", "format_posting"]
