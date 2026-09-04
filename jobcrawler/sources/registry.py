"""Every source by name, and the set a plain run crawls."""

from .apis.arbeitnow import crawl_arbeitnow
from .apis.himalayas import crawl_himalayas
from .apis.remoteok import crawl_remoteok
from .apis.remotive import crawl_remotive
from .ats.ashby import crawl_ashby
from .ats.greenhouse import crawl_greenhouse
from .ats.lever import crawl_lever
from .ats.smartrecruiters import crawl_smartrecruiters
from .ats.workable import crawl_workable
from .blocked import BLOCKED, crawl_blocked
from .boards.arc import crawl_arc
from .boards.builtin import crawl_builtin
from .boards.hn import crawl_hn
from .boards.wwr import crawl_wwr
from .keyed.adzuna import crawl_adzuna
from .keyed.jooble import crawl_jooble
from .keyed.serpapi import crawl_serpapi
from .keyed.usajobs import crawl_usajobs
from .linkedin import crawl_linkedin


SOURCES = {
    "linkedin": crawl_linkedin,
    "greenhouse": crawl_greenhouse,
    "ashby": crawl_ashby,
    "lever": crawl_lever,
    "workable": crawl_workable,
    "smartrecruiters": crawl_smartrecruiters,
    "builtin": crawl_builtin,
    "arc": crawl_arc,
    "wwr": crawl_wwr,
    "hn": crawl_hn,
    "remotive": crawl_remotive,
    "remoteok": crawl_remoteok,
    "arbeitnow": crawl_arbeitnow,
    "himalayas": crawl_himalayas,
    "adzuna": crawl_adzuna,
    "usajobs": crawl_usajobs,
    "jooble": crawl_jooble,
    "serpapi": crawl_serpapi,
}
SOURCES.update({n: crawl_blocked(n) for n in BLOCKED})


DEFAULT_SOURCES = ["linkedin", "greenhouse", "ashby", "lever", "workable",
                   "smartrecruiters", "himalayas", "adzuna", "usajobs",
                   "builtin", "arc", "wwr", "hn"]
