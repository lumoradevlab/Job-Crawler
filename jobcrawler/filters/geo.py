"""Grading where a posting will accept an applicant from."""

import re


# A remote posting is only useful here if a US-based applicant may hold it.
# Boards state that in free text ("Remote - US", "Anywhere", "EMEA only"), so
# every source is graded through us_status() rather than trusted.
STATES = """alabama alaska arizona arkansas california colorado connecticut
delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky
louisiana maine maryland massachusetts michigan minnesota mississippi missouri
montana nebraska nevada hampshire jersey mexico york carolina dakota ohio
oklahoma oregon pennsylvania rhode tennessee texas utah vermont virginia
washington wisconsin wyoming""".split()

ABBREV = set("""AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA
MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA
WV WI WY DC""".split())

# "Remote (US)" and "US-Remote" are two of the commonest ways an ATS states
# a US-remote role, and neither reads as a plain "Remote - US" — so the
# bracket, the em dash and the reversed order all have to be spelled out.
# "us" on its own is deliberately absent: this pattern is also run over job
# bodies, where "come work with us" would otherwise grade as a US posting.
US_HINT = re.compile(
    r"\b(united\s+states|u\.s\.?a?|usa|us[\s-]+only|us[\s-]+based|"
    r"anywhere\s+in\s+the\s+us|remote\s*[-–—,(\[]*\s*us|"
    r"us\s*[-–—]\s*remote|nationwide)\b", re.I
)
WORLDWIDE = re.compile(
    r"\b(anywhere|worldwide|global|remote\s*[-,]?\s*global|"
    r"no\s+preference|any\s+location)\b", re.I
)
NON_US = re.compile(
    r"\b(emea|apac|latam|europe|european|uk\b|united\s+kingdom|ireland|"
    # "New England" is in Massachusetts, so England only counts unprefixed.
    r"(?<!new\s)england|scotland|wales|britain|"
    r"germany|france|spain|portugal|poland|netherlands|india|pakistan|"
    r"philippines|singapore|australia|canada|brazil|argentina|mexico\s+city|"
    r"nigeria|kenya|japan|china|korea|vietnam|indonesia|turkey|romania|"
    r"ukraine|serbia|bulgaria|czech|hungary|greece|israel|uae|dubai)\b", re.I
)
# "Remote (CET ±3)" style timezone fences that rule out a US-based applicant.
NON_US_TZ = re.compile(r"\b(cet|cest|eet|eest|bst|ist|gmt\s*[+±])\b", re.I)


def us_status(text):
    """Grade a location string: 'us', 'worldwide', 'no', or 'unknown'.

    Order matters here. An explicit US signal — the country named, or a
    ", CA" style abbreviation — is checked first and wins outright. Only
    then is the posting tested for another region, and the substring pass
    over STATES runs last of the three: it is much the loosest test, and
    going first it claims "Yorkshire" for New York, "Hampshire" for New
    Hampshire and "Mexico City" for New Mexico.
    """
    if not text:
        return "unknown"
    t = text.strip()
    if NON_US_TZ.search(t) and not US_HINT.search(t):
        return "no"
    if US_HINT.search(t):
        return "us"
    if set(re.findall(r",\s*([A-Z]{2})\b", t)) & ABBREV:
        return "us"
    if NON_US.search(t):
        return "no"
    if any(s in t.lower() for s in STATES):
        return "us"
    if WORLDWIDE.search(t):
        return "worldwide"
    return "unknown"


# Ashby names the country outright, so US-ness never has to be guessed from
# prose the way it does everywhere else.
US_COUNTRY = {"united states", "usa", "us", "united states of america"}
