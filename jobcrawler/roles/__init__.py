"""Named role profiles: which titles count, and what to search for.

The crawler was built for one role. The gate was a pair of module-level
regexes — RELEVANT for the discipline, ROLE for the seniority word — and the
queries were a list in the CLI. That is exactly right for one person crawling
Android jobs, and it is the only thing standing between this and being useful
to anyone else.

A Profile is that pair, named. `--role backend` swaps the discipline regex
and the query list together, which is the part worth doing in one place: a
gate that keeps backend titles while the searches still ask boards for
"Android Developer" returns nothing, and looks like a broken crawler rather
than a mismatched pair.

The ROLE word list is deliberately shared. Every profile wants "engineer",
"developer", "architect", and the handful of role nouns that mean a real
job rather than a department — so it lives once, and a profile only says
what makes it different from the others.
"""

from .profiles import (PROFILES, DEFAULT_ROLE, Profile, resolve,
                       profile_names, combine)

__all__ = ["PROFILES", "DEFAULT_ROLE", "Profile", "resolve",
           "profile_names", "combine"]
