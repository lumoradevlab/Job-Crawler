#!/usr/bin/env python3
"""Crawl remote Android/mobile developer jobs, US-only by default.

Every source is normalised into the same record and graded through the same
gates: an Android/mobile title, a genuine remote flag, and us_status() — which
drops postings fenced to another region, keeps "Worldwide"/"Anywhere" ones (a
US applicant qualifies), and under --strict-us keeps only those naming the US.

Working sources
  linkedin    public "guest" job search endpoint (no login)
  greenhouse  company ATS boards, via the public Greenhouse job-board API
  ashby       company ATS boards, via the public Ashby posting API
  builtin     builtin.com remote board (server-rendered job cards)
  arc         arc.dev remote board (Next.js payload)
  wwr         We Work Remotely RSS feeds
  hn          Hacker News "Ask HN: Who is hiring?" via the official HN API
  remotive / remoteok / arbeitnow
              free public job APIs, no key needed

Unavailable sources, kept so they explain themselves if you ask for them:
  indeed, wellfound, dice, hired, jobright -- see BLOCKED.

Python 3 stdlib only. Run `python3 crawler.py --help` for examples.

--------------------------------------------------------------------------
The code now lives in the jobcrawler package; this module is the entry point
and a compatibility shim over it. Everything that used to be a `crawler.X`
global still is, and assigning to one — `crawler.fetch_json = stub` — still
reaches the modules that call it, so the existing test suite keeps working
unchanged against the split. Both halves of that are deliberate: the tests
are the proof the move changed no behaviour, so they must not be edited to
accommodate it. Delete this file's shim half once callers import the package
directly.
"""

import sys
import time  # noqa: F401  — patched as crawler.time.sleep by the tests
import types

from jobcrawler import cli, config, context, models
from jobcrawler.cli import main
from jobcrawler.filters import geo, rules, workplace
from jobcrawler.net import http
from jobcrawler.parse import dates, html, salary, text
from jobcrawler.pipeline import dedupe
from jobcrawler.report import events, writers
from jobcrawler.sources import blocked, linkedin, registry
from jobcrawler.sources.apis import arbeitnow, himalayas, remoteok, remotive
from jobcrawler.sources.ats import (ashby, boards, discover, greenhouse, lever,
                                    smartrecruiters, workable)
from jobcrawler.sources.boards import arc, builtin, hn, wwr
from jobcrawler.sources.keyed import adzuna, base, jooble, serpapi, usajobs
from jobcrawler.store import archive, seen

# Order is only about which module a shared name is credited to; every
# duplicate below is the same object imported twice, so nothing is shadowed.
_REEXPORT = (http, html, text, salary, dates, config, context, events, models,
             workplace, geo, rules,
             seen, archive, dedupe, writers, boards, linkedin, greenhouse,
             ashby, lever, workable, smartrecruiters, discover, builtin, arc,
             wwr, hn, remotive, remoteok, arbeitnow, himalayas, base, adzuna,
             usajobs, jooble, serpapi, blocked, registry, cli)

for _mod in _REEXPORT:
    for _name, _value in vars(_mod).items():
        if not _name.startswith("_"):
            globals().setdefault(_name, _value)


class _Shim(types.ModuleType):
    """A module whose attribute writes land in the package too.

    `crawler.fetch_json` used to be the single binding every source called
    through, so patching it in a test patched it for all of them. After the
    split each module holds its own reference, and a plain write here would
    rebind only this one. Forwarding the write to every package module that
    already binds the name restores the old behaviour exactly — including the
    restore in tearDown, which is just the same write with the original value.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in list(sys.modules.values()):
            if getattr(mod, "__name__", "").startswith("jobcrawler"):
                if name in vars(mod):
                    setattr(mod, name, value)


sys.modules[__name__].__class__ = _Shim


if __name__ == "__main__":
    main()
