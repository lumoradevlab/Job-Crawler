#!/usr/bin/env python3
"""Tests for the request layer: pacing, retry, and failure accounting.

None of this could be tested before. fetch() was a module-level function that
called urlopen directly, so reaching it meant either hitting the network or
monkeypatching a global; and it reported nothing about what it had done, so
there was nothing to assert on anyway. A Fetcher takes its opener and its
clock as arguments, which is what makes every case below reachable offline.

    python3 test_net.py           # all of it
    python3 test_net.py -v        # naming each case

Stdlib only, like the crawler itself.
"""

import contextlib
import io
import json
import ssl
import threading
import unittest
import urllib.error

from jobcrawler.net.http import Failure, Fetcher, Stats
from jobcrawler.net.ratelimit import HostPolicy, RateLimiter


class FakeResponse:
    """The slice of an http.client.HTTPResponse that _once() touches."""

    def __init__(self, body, encoding=None):
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = {"Content-Encoding": encoding} if encoding else {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_for(*outcomes):
    """An opener that plays the given responses/exceptions, in order.

    The last outcome repeats, so a test that wants "always fails" passes one
    exception rather than counting how many retries the policy will make.
    """
    queue = list(outcomes)
    calls = []

    def opener(req, **kw):
        calls.append(req.full_url)
        out = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(out, Exception):
            raise out
        return FakeResponse(out)

    opener.calls = calls
    return opener


def http_error(code):
    return urllib.error.HTTPError("http://x/", code, "boom", {}, None)


def ssl_failure():
    """What a Mac with no CA bundle actually raises."""
    return urllib.error.URLError(
        ssl.SSLCertVerificationError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer"))


def quiet(fn, *a, **kw):
    """Run something, swallowing the progress chatter it prints to stderr."""
    with contextlib.redirect_stderr(io.StringIO()) as buf:
        result = fn(*a, **kw)
    return result, buf.getvalue()


# ==========================================================================
# The happy path, and the shapes a body can arrive in
# ==========================================================================
class TestGet(unittest.TestCase):

    def fetcher(self, *outcomes, **kw):
        kw.setdefault("sleep", lambda _s: None)
        kw.setdefault("limiter", RateLimiter(policies={}))
        return Fetcher(opener=opener_for(*outcomes), **kw)

    def test_a_body_comes_back_decoded(self):
        f = self.fetcher("hello")
        self.assertEqual(f.get("https://example.com/"), "hello")

    def test_json_is_parsed(self):
        f = self.fetcher(json.dumps({"jobs": [1, 2]}))
        self.assertEqual(f.get_json("https://example.com/"), {"jobs": [1, 2]})

    def test_a_success_is_counted(self):
        f = self.fetcher("hello")
        f.get("https://example.com/")
        self.assertEqual((f.stats.requests, f.stats.ok, f.stats.failed),
                         (1, 1, 0))

    def test_unparseable_json_is_a_failure_not_a_crash(self):
        f = self.fetcher("<html>not json</html>")
        got, _ = quiet(f.get_json, "https://example.com/")
        self.assertIsNone(got)
        self.assertEqual(f.stats.by_kind(), {"InvalidJSON": 1})


# ==========================================================================
# Retry and backoff — the policy that used to be baked into a function
# ==========================================================================
class TestRetry(unittest.TestCase):

    def fetcher(self, *outcomes, **kw):
        kw.setdefault("sleep", lambda _s: None)
        kw.setdefault("limiter", RateLimiter(policies={}))
        self.opener = opener_for(*outcomes)
        return Fetcher(opener=self.opener, **kw)

    def test_a_429_is_retried_then_succeeds(self):
        f = self.fetcher(http_error(429), "hello")
        got, _ = quiet(f.get, "https://example.com/")
        self.assertEqual(got, "hello")
        self.assertEqual(len(self.opener.calls), 2)

    def test_retries_stop_at_the_limit(self):
        f = self.fetcher(http_error(503), tries=3)
        got, _ = quiet(f.get, "https://example.com/")
        self.assertEqual(got, "")
        self.assertEqual(len(self.opener.calls), 3)

    def test_a_403_is_not_retried(self):
        # A bot wall is a decision, not a hiccup; retrying only wastes time.
        f = self.fetcher(http_error(403), tries=4)
        quiet(f.get, "https://example.com/")
        self.assertEqual(len(self.opener.calls), 1)

    def test_a_404_is_an_answer_not_a_failure(self):
        # Board discovery leans on this: an unknown ATS slug 404s, and that
        # has to read as "no such board", not as "the network is down".
        f = self.fetcher(http_error(404))
        self.assertEqual(f.get("https://example.com/"), "")
        self.assertEqual(f.stats.failed, 0)
        self.assertEqual(f.stats.ok, 1)
        self.assertFalse(f.stats.total_blackout())


# ==========================================================================
# Failure accounting — the point of the whole exercise
# ==========================================================================
class TestStats(unittest.TestCase):

    def fetcher(self, *outcomes, **kw):
        kw.setdefault("sleep", lambda _s: None)
        kw.setdefault("limiter", RateLimiter(policies={}))
        return Fetcher(opener=opener_for(*outcomes), **kw)

    def test_a_dead_network_is_a_blackout(self):
        f = self.fetcher(ssl_failure(), tries=1)
        for i in range(3):
            quiet(f.get, f"https://boards-api.greenhouse.io/{i}")
        self.assertTrue(f.stats.total_blackout())
        self.assertEqual(f.stats.failed, 3)

    def test_ssl_failures_are_named_so_they_can_be_fixed(self):
        # "urlopen error" tells nobody anything; the wrapped reason is the
        # part that names the missing CA bundle.
        f = self.fetcher(ssl_failure(), tries=1)
        quiet(f.get, "https://example.com/")
        self.assertEqual(list(f.stats.by_kind()), ["SSLCertVerificationError"])

    def test_one_success_means_it_is_not_a_blackout(self):
        # A quiet week is a real answer; only total silence is ambiguous.
        f = self.fetcher("hello", ssl_failure(), tries=1)
        quiet(f.get, "https://a.example/")
        quiet(f.get, "https://b.example/")
        self.assertFalse(f.stats.total_blackout())
        self.assertEqual(f.stats.failed, 1)

    def test_failures_group_by_host(self):
        f = self.fetcher(ssl_failure(), tries=1)
        quiet(f.get, "https://one.example/a")
        quiet(f.get, "https://one.example/b")
        quiet(f.get, "https://two.example/c")
        self.assertEqual(f.stats.by_host(),
                         {"one.example": 2, "two.example": 1})

    def test_nothing_attempted_is_not_a_blackout(self):
        self.assertFalse(Stats().total_blackout())

    def test_a_failure_remembers_its_host(self):
        self.assertEqual(Failure("https://Example.COM/x", "k", "d").host,
                         "example.com")


# ==========================================================================
# Per-host pacing — replacing thirteen scattered sleeps
# ==========================================================================
class TestRateLimiter(unittest.TestCase):

    def limiter(self, **policies):
        self.slept = []
        return RateLimiter(policies=policies, sleep=self.slept.append)

    def test_an_unlisted_host_is_not_paced(self):
        # The ATS APIs are hit by a six-worker pool with no delay, and that
        # has to stay true or every ATS crawl gets slower for no reason.
        lim = self.limiter()
        lim.wait("https://boards-api.greenhouse.io/v1/boards/stripe/jobs")
        lim.wait("https://boards-api.greenhouse.io/v1/boards/figma/jobs")
        self.assertEqual(self.slept, [])

    def test_the_first_request_to_a_host_does_not_wait(self):
        lim = self.limiter(**{"slow.example": HostPolicy(gap=4.0)})
        self.assertEqual(lim.wait("https://slow.example/a"), 0.0)

    def test_the_second_request_waits_the_gap(self):
        lim = self.limiter(**{"slow.example": HostPolicy(gap=4.0)})
        lim.wait("https://slow.example/a")
        waited = lim.wait("https://slow.example/b")
        self.assertAlmostEqual(waited, 4.0, delta=0.2)

    def test_hosts_are_paced_independently(self):
        lim = self.limiter(**{"slow.example": HostPolicy(gap=4.0)})
        lim.wait("https://slow.example/a")
        self.assertEqual(lim.wait("https://fast.example/a"), 0.0)

    def test_the_host_is_read_case_insensitively(self):
        lim = self.limiter(**{"slow.example": HostPolicy(gap=4.0)})
        lim.wait("https://SLOW.EXAMPLE/a")
        self.assertGreater(lim.wait("https://slow.example/b"), 0)

    def test_concurrent_callers_get_consecutive_slots(self):
        # The reservation happens under the lock; the sleep does not. Four
        # threads asking at once must not all be handed the same slot, or the
        # gap silently becomes "one request per host per burst".
        lim = self.limiter(**{"slow.example": HostPolicy(gap=1.0)})
        waits = []
        lock = threading.Lock()

        def hit():
            w = lim.wait("https://slow.example/x")
            with lock:
                waits.append(w)

        threads = [threading.Thread(target=hit) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(waits), 4)
        # 0, 1, 2, 3 in some order — every caller got a distinct slot.
        self.assertEqual(sorted(round(w) for w in waits), [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main(verbosity=1)
