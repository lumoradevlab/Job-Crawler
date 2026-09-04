"""Reading the workplace — remote, on-site or hybrid — out of the words."""

import re


REMOTE_HINT = re.compile(r"\b(remote|distributed|work\s+from\s+home|wfh)\b", re.I)
# A bare "remote" in a job body means nothing — boilerplate like "if the role
# can be performed remote" appears in postings that are strictly on-site. Only
# these committed phrasings count when the location field itself is silent.
REMOTE_STRONG = re.compile(
    r"(fully\s+remote|100%\s+remote|remote[-\s]first|work\s+from\s+anywhere|"
    r"remote\s*\(\s*us|remote\s*[-,]\s*(us|united\s+states|anywhere)|"
    r"\bus\s*[-,]?\s*remote|this\s+(role|position)\s+is\s+(fully\s+)?remote|"
    r"\bremote\s+(position|role|opportunity|employee)|open\s+to\s+remote|"
    r"remote[-\s]friendly|distributed\s+team)", re.I
)

# LinkedIn's f_WT=2 "Remote" filter leaks — roles titled "(Hybrid)" or
# "- Onsite" come back inside it — and its guest pages carry no workplace-type
# field to check, only Seniority, Employment type, Job function and Industries.
# So the workplace has to be read out of the words instead.
#
# "hybrid" is the trap: in mobile it is also a stack ("hybrid app developer"
# means React Native, not a hybrid office), so it only counts as a workplace
# when it isn't describing the technology.
ONSITE = re.compile(
    r"\b(on-?site|in-office|in\s+the\s+office|"
    r"hybrid(?!\s*(app|application|mobile|cloud|framework|native|stack)))\b",
    re.I,
)
# The mirror of REMOTE_STRONG. A bare "onsite" in a body means nothing either
# — "onsite interviews", "onsite with customers" — so only these committed
# phrasings are allowed to overrule a board's own remote flag.
# A split week is a hybrid job however cheerfully it is worded. These titles
# name both workplaces — "3 days onsite 2 days remote" — so the "onsite is
# forgivable when remote is also offered" rule below reads them as remote and
# keeps them. Aggregator titles are full of this, so it is matched outright.
HYBRID_SPLIT = re.compile(
    r"\b\d\s*(days?|x)\s*(a|per)?\s*week?s?\s*"
    r"(in\s+(the\s+)?office|on-?site|onsite|in-?office|remote)\b"
    r"|\b\d\s*days?\s*(on-?site|onsite|in\s+office|remote)\b"
    r"|\bhybrid\s*[-–:(]\s*\d\s*days?\b", re.I)

ONSITE_STRONG = re.compile(
    r"((this\s+)?(role|position)\s+is\s+(fully\s+)?(on-?site|hybrid)|"
    r"\b(on-?site|hybrid)\s+(role|position|schedule)\b|"
    r"location\s*[-–:]\s*[^.\n]{0,40}\b(hybrid|on-?site)\b|"
    r"require[ds]?\s+to\s+work\s+(on-?site|in\s+the\s+office)|"
    r"\d\s*days?\s+(a|per)\s+week\s+in\s+(the\s+)?office)", re.I
)
