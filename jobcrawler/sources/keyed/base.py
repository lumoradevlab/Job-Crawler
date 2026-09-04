"""Sources that need a (free) API key, and what they say without one."""

import os


# Kept in the same shape as crawl_blocked(): with no key they explain
# themselves and return nothing, so the crawler still runs with zero keys.
KEYED = {
    "adzuna": ("ADZUNA_APP_ID + ADZUNA_APP_KEY",
               "free instantly at https://developer.adzuna.com/signup"),
    "usajobs": ("USAJOBS_KEY + USAJOBS_EMAIL",
                "free instantly at https://developer.usajobs.gov/apirequest/"),
    "jooble": ("JOOBLE_KEY",
               "emailed after review at https://jooble.org/api/about"),
    "serpapi": ("SERPAPI_KEY",
                "250 free searches a month at https://serpapi.com/users/sign_up"),
}


def need_keys(name, *env, report=None):
    """Return the env values, or print why the source is unavailable."""
    vals = [os.environ.get(v, "").strip() for v in env]
    if all(vals):
        return vals
    missing = [v for v, got in zip(env, vals) if not got]
    want, how = KEYED[name]
    if report is not None:
        report.source(name, f"unavailable: set {want} to enable it "
                            f"({how}); missing {', '.join(missing)}")
    return None


# ctx.fetch.get()'s default Accept offers text/html before JSON, and Adzuna honours
# that literally: the same URL that returns jobs to curl returns its HTML docs
# page. Sources that content-negotiate need JSON asked for outright.
JSON_ONLY = {"Accept": "application/json"}
