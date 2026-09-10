"""The profiles themselves: one per discipline, plus how to combine them."""

import re
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Profile:
    """One discipline: the titles it claims, and what to ask boards for.

    `subject` is matched against the title (and, for free-text sources like
    HN, the body). `queries` are what the keyword-taking sources search for.
    The two have to move together — see the package docstring.
    """

    name: str
    summary: str
    subject: str                     # regex source, alternation only
    queries: Tuple[str, ...]
    # Extra role nouns this profile accepts on top of ROLE_WORDS. Only the
    # profiles that are *about* leadership or design set it; see below.
    role_words: str = ""

    def pattern(self):
        return re.compile(r"\b(" + self.subject + r")\b", re.I)

    def role_pattern(self):
        words = ROLE_WORDS + ("|" + self.role_words if self.role_words else "")
        return re.compile(r"\b(" + words + r")\b", re.I)


# Every profile wants these, so they are not repeated in any of them. The
# list is what distinguishes a job title from a department name: "engineer"
# is a role, "engineering" alone is where the role sits — but boards write
# "Engineering Manager" and "Software Development Engineer" too, so both
# forms have to be here.
# A few titles are the entire job and carry no separate role noun —
# "SDET", "SRE", "DevOps" are complete postings on their own. They are role
# words as well as subject words, or the two-part gate refuses them.
STANDALONE = r"sdet|sre|devops|swe"

ROLE_WORDS = (
    STANDALONE + r"|"
    r"developer|engineer|engineering|programmer|architect|"
    r"development|analyst|scientist|administrator|admin|specialist|"
    r"consultant|technician|"
    # "Android Dev", "Full-Stack Dev" — common on smaller boards.
    r"dev\b"
)

# Deliberately NOT in ROLE_WORDS. "Manager", "lead" and "designer" are role
# nouns, but on an engineering profile they let through the jobs *about* the
# work rather than the work: "Product Manager, Mobile" and "Engineering
# Manager" both match a discipline regex and neither is an engineering job.
#
# The profiles that do want them — product, design — add them here instead,
# so widening the crawler never quietly widens the Android gate that was
# here first.
LEADERSHIP_WORDS = r"manager|lead|owner|director|head\s+of|designer"

# Words that make a title a job in a tech org rather than a tech job. These
# are checked against the title after the subject matches, because "Sales
# Engineer" and "Technical Recruiter" both pass a naive discipline gate and
# neither is what anyone here is looking for.
NOT_TECHNICAL = re.compile(
    r"\b(sales|account\s+executive|recruit\w*|talent\s+acquisition|"
    r"business\s+development|marketing|customer\s+success|"
    r"human\s+resources|\bhr\b|payroll|paralegal|attorney|"
    r"teacher|instructor|tutor|nurse|driver|warehouse)\b", re.I
)


PROFILES = {
    "android": Profile(
        "android", "Android and cross-platform mobile",
        r"android|kotlin|jetpack\s*compose|mobile|react\s*native|flutter|"
        r"ios\s*/?\s*android",
        ("Android Developer", "Android Engineer", "Mobile Developer",
         "Mobile Engineer", "Android Software Engineer", "Kotlin Developer",
         "Mobile Software Engineer", "Senior Android Developer"),
    ),
    "ios": Profile(
        "ios", "iOS and Apple platforms",
        r"ios|swift|swiftui|objective[-\s]?c|apple|iphone|ipad|macos",
        ("iOS Developer", "iOS Engineer", "Swift Developer",
         "Senior iOS Engineer", "Mobile Engineer iOS"),
    ),
    "mobile": Profile(
        "mobile", "All mobile: Android, iOS and cross-platform",
        r"android|ios|kotlin|swift|swiftui|jetpack\s*compose|mobile|"
        r"react\s*native|flutter|objective[-\s]?c",
        ("Mobile Developer", "Mobile Engineer", "Android Developer",
         "iOS Developer", "React Native Developer", "Flutter Developer"),
    ),
    "frontend": Profile(
        "frontend", "Front-end and web UI",
        r"front[-\s]?end|frontend|react|vue|angular|svelte|javascript|"
        r"typescript|web\s+developer|ui\s+engineer|web\s+ui",
        ("Frontend Developer", "Frontend Engineer", "React Developer",
         "JavaScript Developer", "TypeScript Engineer", "Web Developer",
         "UI Engineer"),
    ),
    "backend": Profile(
        "backend", "Back-end, APIs and services",
        r"back[-\s]?end|backend|server[-\s]?side|api|micro\s?services?|"
        r"golang|\bgo\b|rust|java|python|ruby|node(\.?js)?|scala|elixir|"
        r"\.net|c#|php|django|rails|spring",
        ("Backend Developer", "Backend Engineer", "Software Engineer",
         "Python Developer", "Java Developer", "Go Developer",
         "Node.js Developer", "API Engineer"),
    ),
    "fullstack": Profile(
        "fullstack", "Full-stack web",
        r"full[-\s]?stack|fullstack|full\s+stack",
        ("Full Stack Developer", "Full Stack Engineer",
         "Fullstack Software Engineer", "Full-Stack Web Developer"),
    ),
    "devops": Profile(
        "devops", "DevOps, SRE, platform and infrastructure",
        r"devops|sre|site\s+reliability|platform|infrastructure|cloud|"
        r"kubernetes|k8s|docker|terraform|aws|azure|gcp|ci/?cd|"
        r"systems?\s+engineer",
        ("DevOps Engineer", "Site Reliability Engineer", "Platform Engineer",
         "Infrastructure Engineer", "Cloud Engineer", "Kubernetes Engineer",
         "SRE"),
    ),
    "data": Profile(
        "data", "Data engineering, science and analytics",
        r"data|analytics|machine\s+learning|\bml\b|\bai\b|deep\s+learning|"
        r"etl|warehouse|bigquery|spark|hadoop|airflow|snowflake|"
        r"business\s+intelligence|\bbi\b",
        ("Data Engineer", "Data Scientist", "Data Analyst",
         "Machine Learning Engineer", "Analytics Engineer",
         "ML Engineer", "Business Intelligence Analyst"),
    ),
    "qa": Profile(
        "qa", "QA, test and automation",
        r"\bqa\b|quality\s+assurance|test|testing|automation|sdet",
        ("QA Engineer", "Test Engineer", "Automation Engineer", "SDET",
         "Quality Assurance Engineer", "QA Automation"),
    ),
    "security": Profile(
        "security", "Security engineering and appsec",
        r"security|appsec|infosec|cyber|penetration|pentest|"
        r"vulnerability|compliance|soc\s?2|iam|cryptograph",
        ("Security Engineer", "Application Security Engineer",
         "Cybersecurity Engineer", "Penetration Tester",
         "Security Analyst", "Cloud Security Engineer"),
    ),
    "embedded": Profile(
        "embedded", "Embedded, firmware and hardware-adjacent",
        r"embedded|firmware|rtos|microcontroller|\bfpga\b|verilog|vhdl|"
        r"hardware|robotics|iot",
        ("Embedded Software Engineer", "Firmware Engineer",
         "Embedded Systems Engineer", "IoT Engineer"),
    ),
    "gamedev": Profile(
        "gamedev", "Games and real-time graphics",
        r"game|unity|unreal|graphics|shader|gameplay|3d\s+engine",
        ("Game Developer", "Game Engineer", "Unity Developer",
         "Unreal Engine Developer", "Graphics Engineer"),
    ),
    "itsupport": Profile(
        "itsupport", "IT support, sysadmin and networking",
        r"\bit\b|help\s?desk|service\s?desk|support|sysadmin|"
        r"systems?\s+admin(istrator)?|network|desktop|technical\s+support",
        ("IT Support Specialist", "Systems Administrator",
         "Network Engineer", "Help Desk Technician", "IT Technician",
         "Desktop Support"),
    ),
    "product": Profile(
        "product", "Technical product management",
        r"product\s+manager|product\s+owner|technical\s+program|\btpm\b|"
        r"program\s+manager|product\s+lead",
        ("Technical Product Manager", "Product Manager Software",
         "Technical Program Manager", "Product Owner"),
        role_words=LEADERSHIP_WORDS,
    ),
    "design": Profile(
        "design", "Product design, UX and UI",
        r"designer|design|\bux\b|\bui\b|user\s+experience|"
        r"user\s+interface|interaction|visual\s+design|product\s+design",
        ("Product Designer", "UX Designer", "UI Designer",
         "UX Engineer", "Design Systems Engineer"),
        role_words=LEADERSHIP_WORDS,
    ),
}

# "software" is every engineering profile at once, for someone who wants the
# discipline rather than a specialism. Built from the others so a change to
# one of them reaches it, rather than being a sixteenth list to maintain.
_SOFTWARE_PARTS = ("android", "ios", "frontend", "backend", "fullstack",
                   "devops", "data", "qa", "security", "embedded", "gamedev")
PROFILES["software"] = Profile(
    "software", "Every software engineering discipline",
    "|".join(PROFILES[p].subject for p in _SOFTWARE_PARTS)
    + r"|software|programmer|computer\s+scien|engineer",
    ("Software Engineer", "Software Developer", "Senior Software Engineer",
     "Backend Engineer", "Frontend Engineer", "Full Stack Engineer",
     "DevOps Engineer", "Data Engineer"),
)

# "all" adds the adjacent technical roles — IT, product, design — to that.
PROFILES["all"] = Profile(
    "all", "All software and IT, including product, design and IT support",
    "|".join(PROFILES[p].subject
             for p in _SOFTWARE_PARTS + ("itsupport", "product", "design"))
    + r"|software|programmer|computer\s+scien|engineer|technical",
    ("Software Engineer", "Software Developer", "DevOps Engineer",
     "Data Engineer", "QA Engineer", "Security Engineer",
     "IT Support Specialist", "Product Manager", "Product Designer"),
    role_words=LEADERSHIP_WORDS,
)

# Unchanged for anyone already running this: a plain run still crawls Android.
DEFAULT_ROLE = "android"


def profile_names():
    """Every profile name, for --help and the choices list."""
    return sorted(PROFILES)


def resolve(name):
    """One profile by name, or a KeyError naming what does exist."""
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError("unknown role %r; try one of: %s"
                       % (name, ", ".join(profile_names())))


def combine(names):
    """Fuse several profiles into one.

    --role backend devops is two disciplines, not two runs: the queries are
    concatenated and the subjects are alternated, so a title matching either
    passes a single gate. Duplicate queries are dropped because two profiles
    asking for "Software Engineer" would otherwise cost double the requests
    for identical results.
    """
    chosen = [resolve(n) for n in names] or [resolve(DEFAULT_ROLE)]
    if len(chosen) == 1:
        return chosen[0]
    queries = list(dict.fromkeys(q for p in chosen for q in p.queries))
    extra = "|".join(dict.fromkeys(
        w for p in chosen if p.role_words for w in [p.role_words]))
    return Profile(
        "+".join(p.name for p in chosen),
        "; ".join(p.summary for p in chosen),
        "|".join(p.subject for p in chosen),
        tuple(queries),
        role_words=extra,
    )
