"""Sources that cannot be crawled — kept so they explain themselves."""


BLOCKED = {
    "indeed": "HTTP 403 — Cloudflare bot wall; Indeed also retired its public API.",
    "wellfound": "Cloudflare Turnstile challenge; the HTML carries no job data.",
    "dice": "Search API returns 403 — the public frontend key has been rotated.",
    "hired": "Hired.com no longer exists — it now redirects to LHH.",
    "jobright": "Server-rendered results ignore the search keyword — asking for "
                "'android developer' returns unrelated marketing roles. The real "
                "results come from an API that requires a logged-in account.",
}


def crawl_blocked(name):
    def _fn(cfg, ctx):
        print(f"[{name}] unavailable: {BLOCKED[name]}")
        return []
    return _fn
