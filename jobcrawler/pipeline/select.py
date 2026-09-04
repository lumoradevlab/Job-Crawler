"""Gate, de-duplicate and order what the sources returned.

This was the middle of main(): a 40-line loop mixing three separate decisions
— whether a posting passes the rules, which of two records for one job to
keep, and what the run should be able to explain afterwards. Pulled out, each
one is a function over a list, which is the only thing needed to test it.
"""

from ..filters.geo import us_status
from ..filters.rules import rejection
from ..store.seen import job_key
from .dedupe import SOURCE_RANK, dedupe_key

SALARY_FIELDS = ("salary_min", "salary_max", "salary_currency",
                 "salary_predicted")


def _rank(posting):
    # An unranked source loses every head-to-head; 50 is well past the
    # highest real rank, so a source added without a rank is merely
    # deprioritised rather than silently winning.
    return SOURCE_RANK.get(posting.source, 50)


def _inherit_salary(winner, loser):
    """Keep pay the losing record knew and the winner doesn't."""
    if loser is not None and loser.salary_min and not winner.salary_min:
        for f in SALARY_FIELDS:
            setattr(winner, f, getattr(loser, f))


def select(postings, filters, explain=False):
    """Apply the gate, collapse duplicates, order the survivors.

    Returns (jobs, rejected). `rejected` is only populated when explain is
    set, since --why is the only thing that reads it and building it for
    every run would keep every dropped posting alive in memory.
    """
    best, rejected = {}, []

    for j in postings:
        why = rejection(j, filters)
        if why:
            if explain:
                rejected.append((j, why))
            continue

        j.us = j.us or us_status(j.location)
        key = dedupe_key(j)
        prior = best.get(key)

        # Two sources describing one job is common now that aggregators are
        # in the mix, and the first one crawled is not the one worth keeping:
        # a company's own ATS link outlives the aggregator's redirect and
        # states its location honestly. So collapse by rank, not by arrival.
        if prior is None or _rank(j) < _rank(prior):
            _inherit_salary(j, prior)
            if prior is not None and explain:
                rejected.append((prior, "duplicate: %s carries the same job "
                                        "on a better link" % j.source))
            best[key] = j
        else:
            _inherit_salary(prior, j)
            if explain:
                rejected.append((j, "duplicate: kept the %s record instead"
                                    % prior.source))

    jobs = list(best.values())
    if filters.min_salary:
        jobs = [j for j in jobs
                if not j.salary_max or j.salary_max >= filters.min_salary]
    jobs.sort(key=lambda j: (j.posted, j.source), reverse=True)
    return jobs, rejected


def split_new(jobs, seen, today):
    """Stamp first_seen on every job and return the ones never reported.

    Both halves matter to the caller: the new ones are what gets reported,
    and everything matched is what gets archived — narrowing the report is a
    presentation choice and never a reason to lose data.
    """
    fresh = []
    for j in jobs:
        prior = seen.get(job_key(j))
        j.first_seen = (prior or {}).get("first_seen", today)
        if prior is None:
            fresh.append(j)
    return fresh
