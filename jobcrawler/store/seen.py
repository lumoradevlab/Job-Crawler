"""Seen-job state — so a repeat run only reports what is new."""

import json
from datetime import datetime


# The boards give no "changed since" parameter, so a run always re-fetches the
# same listings; what this avoids is re-reporting them. Identity is the job
# URL, which is stable across runs on every source here.
def job_key(job):
    # Keep the query string: several boards carry the job id there
    # (…/jobs/?gh_jid=4916795), so stripping it merges unrelated postings.
    url = job.url.strip().rstrip("/")
    if url:
        return url
    return "{}|{}|{}".format(job.source, job.title.lower().strip(),
                             job.company.lower().strip())


META = "_meta"  # run bookkeeping, stored alongside the seen jobs


def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_run(state, today, window_days, succeeded):
    """Remember this run — advancing only the sources that actually worked."""
    meta = state.setdefault(META, {})
    meta["last_run"] = today
    meta["window_days"] = window_days
    per = meta.setdefault("sources", {})
    for name in succeeded:
        per[name] = today
    return state


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, ensure_ascii=False)


def catchup_days(state, wanted, today, sources=()):
    """How far back this run actually needs to look.

    After a 60-day sweep there is no reason to ask for 60 days again the
    next morning — only for what appeared since. Two spare days absorb
    postings that land late or shift timezone.

    Measured per source when `sources` is given, and from the oldest of them.
    The run-wide "last_run" was wrong in a way that lost postings: it advanced
    even when a source had failed, so the next morning's window covered only
    the day since the failed run, and everything the broken source would have
    returned had already fallen outside it. A source down for a week must be
    asked for a week.
    """
    meta = state.get(META) or {}
    if sources:
        stamps = [(meta.get("sources") or {}).get(s) for s in sources]
        # A source with no recorded success — never run, or failing since
        # before this bookkeeping existed — needs the whole window.
        if not all(stamps):
            return wanted
        last = min(stamps)
    else:
        last = meta.get("last_run")
    if not last:
        return wanted
    try:
        gap = (datetime.strptime(today, "%Y-%m-%d")
               - datetime.strptime(last, "%Y-%m-%d")).days
    except ValueError:
        return wanted
    return max(1, min(wanted, gap + 2))
