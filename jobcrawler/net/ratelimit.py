"""Per-host request pacing.

Politeness used to be thirteen `time.sleep(1)` calls scattered through the
sources, which made it invisible and uneven: the ATS crawlers ran six threads
flat out against one API while the aggregators slept a second between pages
on a single thread. Both are defensible — an ATS API is built to be polled and
LinkedIn will throttle you for looking eager — but neither was stated anywhere,
so neither could be tuned or even found.

The pacing is per host rather than global for exactly that reason. One global
rule would have to be as slow as the slowest host, which would turn a 46-board
Greenhouse sweep into a several-minute crawl for no benefit to anyone.
"""

import random
import threading
import time
import urllib.parse


class HostPolicy:
    """How hard one host may be hit: a gap between requests, and a jitter.

    `gap` is the minimum seconds between the *starts* of two requests to the
    host, counted across threads. `jitter` is added to each wait so a pool of
    workers doesn't fall into lockstep and arrive as a burst.
    """

    __slots__ = ("gap", "jitter")

    def __init__(self, gap=0.0, jitter=0.0):
        self.gap = gap
        self.jitter = jitter

    def __repr__(self):
        return f"HostPolicy(gap={self.gap}, jitter={self.jitter})"


# Seeded to reproduce what each source already did, so this commit changes
# pacing for nobody. The ATS and public-API hosts were being hit by a thread
# pool with no delay at all and have never complained; keeping them at 0 is
# what stops a "tidy up the sleeps" change from making every run slower.
#
# LinkedIn is the exception that motivated the whole idea: it is the one host
# that throttles, its pacing was already a CLI flag, and it is the only place
# where the gap wants to be several seconds.
DEFAULT_POLICIES = {
    "www.linkedin.com": HostPolicy(gap=4.0, jitter=1.5),
    "hacker-news.firebaseio.com": HostPolicy(gap=0.025),
    "himalayas.app": HostPolicy(gap=1.0),
    "remotive.com": HostPolicy(gap=1.0),
    "www.arbeitnow.com": HostPolicy(gap=1.0),
    "arc.dev": HostPolicy(gap=1.0),
    "weworkremotely.com": HostPolicy(gap=1.0),
    "builtin.com": HostPolicy(gap=1.0),
    "api.adzuna.com": HostPolicy(gap=1.0),
    "data.usajobs.gov": HostPolicy(gap=1.0),
    "jooble.org": HostPolicy(gap=1.0),
    "serpapi.com": HostPolicy(gap=1.0),
}

# Everything unnamed — every ATS API — goes as fast as the thread pool allows,
# which is what it did before this module existed.
DEFAULT_POLICY = HostPolicy()


class RateLimiter:
    """Holds the last-request time per host and sleeps to keep the gap.

    Thread-safe by design, because the ATS sources call it from a six-worker
    pool. The lock is held only while reserving a slot, never while sleeping —
    holding it across the sleep would serialise the pool down to one worker
    and silently undo the concurrency the ATS crawls depend on.
    """

    def __init__(self, policies=None, default=None, sleep=time.sleep):
        self.policies = dict(DEFAULT_POLICIES if policies is None else policies)
        self.default = default or DEFAULT_POLICY
        self._sleep = sleep
        self._next_free = {}
        self._lock = threading.Lock()

    def policy_for(self, url):
        host = urllib.parse.urlparse(url).netloc.lower()
        return self.policies.get(host, self.default)

    def set_policy(self, host, policy):
        self.policies[host.lower()] = policy

    def wait(self, url):
        """Block until this host may be hit again. Returns seconds waited."""
        policy = self.policy_for(url)
        if policy.gap <= 0 and policy.jitter <= 0:
            return 0.0

        host = urllib.parse.urlparse(url).netloc.lower()
        pause = policy.gap + (random.uniform(0, policy.jitter)
                              if policy.jitter else 0.0)
        now = time.monotonic()
        with self._lock:
            # Reserve this host's next slot before releasing the lock, so two
            # threads asking at once get consecutive slots rather than the same
            # one. Whoever reserved a slot in the past starts immediately.
            free_at = self._next_free.get(host, now)
            start = max(now, free_at)
            self._next_free[host] = start + pause
        delay = start - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)
