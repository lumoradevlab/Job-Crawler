"""HTTP, as an object the sources are handed rather than a global they call.

`fetch()` used to be a module-level function with the retry policy baked in,
called from two dozen places. That made three things impossible: configuring
it, substituting it in a test, and — the one that actually bit — noticing that
every request had failed. It swallowed each error, printed to stderr and
returned "", so a machine with no CA certificates reported "0 postings
scanned" and looked exactly like a quiet week on the job market.

A Fetcher counts what it swallows. The run can then say "46 of 46 requests
failed" instead of reporting a confident zero, which is the same argument
report_rejections() makes one layer up: silence is ambiguous, so measure it.

The module-level fetch()/fetch_json() remain, delegating to a default
instance, so callers that haven't been handed a Fetcher yet still work.
"""

import gzip
import json
import random
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .ratelimit import RateLimiter

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
}

RETRY_CODES = (429, 500, 502, 503)


class Failure:
    """One request that produced nothing, kept so the run can report it."""

    __slots__ = ("url", "host", "kind", "detail")

    def __init__(self, url, kind, detail):
        self.url = url
        self.host = urllib.parse.urlparse(url).netloc.lower()
        self.kind = kind
        self.detail = detail

    def __repr__(self):
        return f"Failure({self.host}, {self.kind}, {self.detail})"


class Stats:
    """What a Fetcher did, so a run can report it instead of guessing."""

    def __init__(self):
        self.requests = 0        # attempts that reached the network layer
        self.ok = 0              # returned a body (a 404 counts as answered)
        self.failures = []
        self._lock = threading.Lock()

    @property
    def failed(self):
        return len(self.failures)

    def record_ok(self):
        with self._lock:
            self.requests += 1
            self.ok += 1

    def record_failure(self, failure):
        with self._lock:
            self.requests += 1
            self.failures.append(failure)

    def by_kind(self):
        out = {}
        for f in self.failures:
            out[f.kind] = out.get(f.kind, 0) + 1
        return out

    def by_host(self):
        out = {}
        for f in self.failures:
            out[f.host] = out.get(f.host, 0) + 1
        return out

    def total_blackout(self):
        """True when every request failed — a dead network, not a quiet week.

        This is the case worth shouting about. Any successes at all mean the
        crawler was talking to something and an empty result is a real answer.
        """
        return self.requests > 0 and self.ok == 0


class Fetcher:
    """GET a URL, with retry, per-host pacing and failure accounting."""

    def __init__(self, tries=4, timeout=20, limiter=None, backoff=5.0,
                 sleep=time.sleep, opener=None, context=None, stats=None,
                 report=None):
        self.tries = tries
        self.timeout = timeout
        self.limiter = RateLimiter() if limiter is None else limiter
        self.backoff = backoff
        self.stats = stats if stats is not None else Stats()
        self._sleep = sleep
        self._opener = opener or urllib.request.urlopen
        self._context = context
        # A plain stderr print when nobody supplied a reporter, so a
        # Fetcher built on its own still says when a request failed.
        self._warn = report.warn if report is not None else (
            lambda m: print(m, file=sys.stderr))

    # -- the two calls every source makes ---------------------------------
    def get(self, url, tries=None, timeout=None, headers=None):
        """GET a URL, returning decoded text. "" if it could not be read."""
        tries = self.tries if tries is None else tries
        h = dict(BASE_HEADERS)
        if headers:
            h.update(headers)

        for attempt in range(1, tries + 1):
            self.limiter.wait(url)
            try:
                body = self._once(url, h, timeout)
            except urllib.error.HTTPError as e:
                # A 404 is an answer: the board said this slug does not exist.
                # Discovery depends on telling that apart from a dead network.
                if e.code == 404:
                    self.stats.record_ok()
                    return ""
                if e.code in RETRY_CODES and attempt < tries:
                    wait = self.backoff * attempt + random.uniform(0, 2)
                    self._warn(f"  ! HTTP {e.code}, backing off {wait:.0f}s "
                               f"(try {attempt}/{tries})")
                    self._sleep(wait)
                    continue
                self._fail(url, f"HTTP {e.code}", str(e), f"HTTP {e.code}")
                return ""
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < tries:
                    self._sleep(self.backoff * attempt)
                    continue
                self._fail(url, _kind_of(e), str(e), str(e))
                return ""
            else:
                self.stats.record_ok()
                return body
        return ""

    def get_json(self, url, **kw):
        text = self.get(url, **kw)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            self._fail(url, "InvalidJSON", str(e), f"invalid JSON: {e}")
            return None

    def post_json(self, url, payload, headers=None, timeout=None):
        """POST a JSON body and read a JSON reply. None if it could not be.

        Only Jooble needs this, but it needs to be here rather than hand-rolled
        in that source: a request the Fetcher never sees is a request whose
        failure never reaches the run summary, and a source silently exempt
        from the blackout check is the exact bug this class exists to catch.
        """
        h = dict(BASE_HEADERS)
        h["Content-Type"] = "application/json"
        if headers:
            h.update(headers)
        body = json.dumps(payload).encode("utf-8")
        self.limiter.wait(url)
        req = urllib.request.Request(url, data=body, headers=h)
        kw = {"timeout": self.timeout if timeout is None else timeout}
        if self._context is not None:
            kw["context"] = self._context
        try:
            with self._opener(req, **kw) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self._fail(url, _kind_of(e), str(e), f"{type(e).__name__}: {e}")
            return None
        self.stats.record_ok()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            self._fail(url, "InvalidJSON", str(e), f"invalid JSON: {e}")
            return None

    # -- internals ---------------------------------------------------------
    def _once(self, url, headers, timeout):
        req = urllib.request.Request(url, headers=headers)
        kw = {"timeout": self.timeout if timeout is None else timeout}
        if self._context is not None:
            kw["context"] = self._context
        with self._opener(req, **kw) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")

    def _fail(self, url, kind, detail, show=None):
        # `kind` groups the failure for the run summary; `show` is what the
        # old fetch() printed, kept verbatim because the SSL text it carries
        # is the part that tells you how to fix it.
        self._warn(f"  ! {show or kind} on {url}")
        self.stats.record_failure(Failure(url, kind, detail))


def _kind_of(exc):
    """Name the failure the way a user can act on.

    A missing CA bundle arrives as URLError wrapping SSLCertVerifyError, and
    "urlopen error" is useless to anyone trying to fix it — so the wrapped
    reason is what gets named.
    """
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLError):
        return type(reason).__name__
    if isinstance(reason, Exception):
        return type(reason).__name__
    return type(exc).__name__


# The default instance the module-level helpers use. Sources that have been
# handed their own Fetcher never touch this one.
DEFAULT = Fetcher()


def fetch(url, tries=4, timeout=20, headers=None):
    """GET a URL through the default Fetcher. Backs off on 429/5xx."""
    return DEFAULT.get(url, tries=tries, timeout=timeout, headers=headers)


def fetch_json(url, **kw):
    return DEFAULT.get_json(url, **kw)


def post_json(url, payload, **kw):
    return DEFAULT.post_json(url, payload, **kw)
