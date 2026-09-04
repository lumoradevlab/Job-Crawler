"""One GET, with the retry and backoff policy every source shares."""

import gzip
import json
import random
import sys
import time
import urllib.error
import urllib.request


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def fetch(url, tries=4, timeout=20, headers=None):
    """GET a URL, returning decoded text. Backs off on 429/5xx."""
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    }
    if headers:
        h.update(headers)
    delay = 5.0
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if e.code in (429, 500, 502, 503) and attempt < tries:
                wait = delay * attempt + random.uniform(0, 2)
                print(f"  ! HTTP {e.code}, backing off {wait:.0f}s "
                      f"(try {attempt}/{tries})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  ! HTTP {e.code} on {url}", file=sys.stderr)
            return ""
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries:
                time.sleep(delay * attempt)
                continue
            print(f"  ! {e} on {url}", file=sys.stderr)
            return ""
    return ""


def fetch_json(url, **kw):
    text = fetch(url, **kw)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None
