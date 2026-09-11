"""Which country a run is looking for work in.

The crawler graded every posting through us_status(): does this role accept
an applicant based in the United States? That is the right question and the
wrong constant. A Canadian applicant wants the same test with a different
answer, and the two cannot be the same function with a flag — the region
lists have to swap, because Canada is on the US gate's *reject* list and
would otherwise drop every job it is supposed to keep.

So a Country carries the three things that differ: what counts as home
("Remote - Canada", ", ON"), what counts as elsewhere, and the sub-national
names a bare city string might carry. Everything above this — the worldwide
rule, the ordering, the reason strings — is shared and lives in geo.py.

Adding one is a matter of writing the three patterns. The two here are the
ones this crawler has sources for: Adzuna indexes both, and the ATS boards
that rank highest carry North American roles either way.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Country:
    """One country's answer to "will this posting take me?"."""

    code: str
    name: str
    hint: object          # compiled: the country named outright
    abbrev: frozenset     # ", CA" style sub-national codes
    regions: object       # compiled: sub-national names, matched loosely
    foreign: object       # compiled: regions that rule this applicant out
    # The other country's postal codes, read in the ", XX" position. Without
    # these "Austin, TX" grades 'unknown' to a Canadian rather than 'no',
    # and an unlabelled posting is kept where a foreign one is dropped.
    foreign_abbrev: frozenset
    adzuna: str           # Adzuna's index for it
    linkedin: str         # the location string LinkedIn resolves
    # Shown on the message. Two feeds in one chat otherwise look identical
    # for a worldwide posting, where the location names no country at all.
    flag: str = ""


# -- shared pieces -----------------------------------------------------------

# Regions that exclude a North American applicant whichever side of the
# border they are on. Split out because both countries reject all of these,
# and only the other country's name differs between the two lists.
_OVERSEAS = (
    r"emea|apac|latam|europe|european|uk\b|united\s+kingdom|ireland|"
    # "New England" is in Massachusetts, so England only counts unprefixed.
    r"(?<!new\s)england|scotland|wales|britain|"
    r"germany|france|spain|portugal|poland|netherlands|india|pakistan|"
    r"philippines|singapore|australia|brazil|argentina|"
    r"nigeria|kenya|japan|china|korea|vietnam|indonesia|turkey|romania|"
    r"ukraine|serbia|bulgaria|czech|hungary|greece|israel|uae|dubai"
)


# -- the United States -------------------------------------------------------

US_STATES = """alabama alaska arizona arkansas california colorado connecticut
delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky
louisiana maine maryland massachusetts michigan minnesota mississippi missouri
montana nebraska nevada hampshire jersey mexico york carolina dakota ohio
oklahoma oregon pennsylvania rhode tennessee texas utah vermont virginia
washington wisconsin wyoming""".split()

# Provinces and territories, plus the cities big enough that a posting names
# them without the province. "Ontario" is also a town in California, which is
# why this list is only reached after the country-name and postal-abbreviation
# tests have both failed.
CA_REGIONS = """ontario quebec québec alberta manitoba saskatchewan
brunswick scotia newfoundland labrador edward nunavut yukon columbia
toronto montreal montréal vancouver calgary edmonton ottawa winnipeg
mississauga hamilton kitchener waterloo halifax victoria saskatoon regina
burnaby richmond markham vaughan gatineau laval""".split()

CA_REGION_WORDS = tuple(re.escape(w) for w in CA_REGIONS)

# US cities named without their state often enough to matter. This exists
# for one ambiguity: "CA" is California and also Canada's ISO code, so a
# Canadian run cannot settle "San Francisco, CA" from the abbreviation —
# putting CA in its foreign list would reject "Remote - CA" meaning Canada.
# The city name is the unambiguous half, so that is what is read.
US_CITY_WORDS = tuple(re.escape(w) for w in
                      ("san francisco", "los angeles", "new york city",
                       "seattle", "boston", "chicago", "austin", "denver",
                       "atlanta", "portland", "phoenix", "dallas", "houston",
                       "miami", "philadelphia", "san diego", "san jose",
                       "palo alto", "mountain view", "sunnyvale", "cupertino",
                       "bellevue", "redmond", "oakland", "berkeley"))

# Named before either Country is built, since each rejects the other's.
US_STATE_WORDS = tuple(re.escape(w) for w in US_STATES)

US = Country(
    code="us",
    name="United States",
    # "Remote (US)" and "US-Remote" are two of the commonest ways an ATS
    # states a US-remote role, so the bracket, the em dash and the reversed
    # order all have to be spelled out. "us" alone is deliberately absent:
    # this runs over job bodies, where "come work with us" would grade as a
    # US posting.
    hint=re.compile(
        r"\b(united\s+states|u\.s\.?a?|usa|us[\s-]+only|us[\s-]+based|"
        r"anywhere\s+in\s+the\s+us|remote\s*[-–—,(\[]*\s*us|"
        r"us\s*[-–—]\s*remote|nationwide)\b", re.I),
    abbrev=frozenset("""AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA
        ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD
        TN TX UT VT VA WA WV WI WY DC""".split()),
    regions=re.compile("|".join(re.escape(s) for s in US_STATES), re.I),
    # Canadian provinces and big cities too: without them "Toronto, ON"
    # falls through every test and grades 'unknown' rather than 'no', which
    # --strict-us would keep as an unlabelled posting.
    foreign=re.compile(r"\b(" + _OVERSEAS + r"|canada|canadian|"
                       r"mexico\s+city|" + "|".join(CA_REGION_WORDS)
                       + r")\b", re.I),
    foreign_abbrev=frozenset("AB BC MB NB NL NS NT NU ON PE QC SK YT".split()),
    adzuna="us",
    linkedin="United States",
    flag="🇺🇸",
)


# -- Canada ------------------------------------------------------------------

CA = Country(
    code="ca",
    name="Canada",
    hint=re.compile(
        r"\b(canada|canadian|ca[\s-]+only|canada[\s-]+based|"
        r"anywhere\s+in\s+canada|remote\s*[-–—,(\[]*\s*canada|"
        r"canada\s*[-–—]\s*remote|remote\s*\(\s*ca\s*\))\b", re.I),
    # Two-letter postal abbreviations. "ON" and "NU" are ordinary English
    # words, but they are only consulted in the ", XX" position — a comma,
    # optional space, then the code — which prose does not produce.
    abbrev=frozenset("AB BC MB NB NL NS NT NU ON PE QC SK YT".split()),
    regions=re.compile("|".join(re.escape(s) for s in CA_REGIONS), re.I),
    # The mirror of the US list: everything overseas, plus the US itself.
    # US state names too, for the mirror of the reason above: "Austin, TX"
    # must read as 'no' to a Canadian applicant, not as 'unknown'.
    foreign=re.compile(r"\b(" + _OVERSEAS + r"|united\s+states|u\.s\.?a?|"
                       r"usa|\bus\b|us[\s-]+only|us[\s-]+based|mexico|"
                       + "|".join(US_STATE_WORDS + US_CITY_WORDS)
                       + r")\b", re.I),
    foreign_abbrev=frozenset("""AL AK AZ AR CO CT DE FL GA HI ID IL IN IA KS
        KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI
        SC SD TN TX UT VT VA WA WV WI WY DC""".split()),
    adzuna="ca",
    linkedin="Canada",
    flag="🇨🇦",
)


COUNTRIES = {c.code: c for c in (US, CA)}
DEFAULT_COUNTRY = "us"


def resolve(code):
    """One country by code, or a ValueError naming what does exist."""
    try:
        return COUNTRIES[(code or "").lower()]
    except KeyError:
        raise ValueError("unknown country %r; try one of: %s"
                         % (code, ", ".join(sorted(COUNTRIES))))


def country_codes():
    return sorted(COUNTRIES)
