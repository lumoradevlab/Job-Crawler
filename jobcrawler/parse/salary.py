"""Reading pay out of prose and normalising every rate to yearly dollars."""

import re


# Aggregators state pay as prose — "$100k - $120k", "$80 per hour" — where
# the ATS boards give numbers. Hourly rates are annualised at 2080h so one
# --min-salary threshold can judge both.
SALARY_TEXT = re.compile(
    r"\$\s*([\d,.]+)\s*(k)?\s*(?:-|to|–)?\s*(?:\$\s*([\d,.]+)\s*(k)?)?"
    r"\s*(per\s+hour|/\s*hr|an\s+hour|hourly)?", re.I)


def parse_salary(text):
    """Return (min, max) in yearly dollars, or (None, None)."""
    if not text:
        return None, None
    m = SALARY_TEXT.search(text)
    if not m:
        return None, None

    def num(raw, k):
        if not raw:
            return None
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            return None
        if k:
            v *= 1000
        return v

    lo, hi = num(m.group(1), m.group(2)), num(m.group(3), m.group(4))
    if lo and m.group(5):                 # an hourly rate, annualised
        lo, hi = lo * 2080, (hi * 2080 if hi else None)
    # A bare "$120" is a rate fragment, not a salary; treat it as unstated.
    if lo and lo < 1000:
        return None, None
    return lo, (hi or None)


# Boards that state a rate say which interval it is in. An hourly figure
# left raw reads as an $80 salary and is dropped by any realistic
# --min-salary, so everything is normalised to yearly dollars at 2080h.
HOURLY_RATE = re.compile(r"^(ph|per\s*hour|hourly|hour)$", re.I)


def annualise(amount, interval):
    """Normalise a stated rate to yearly dollars, or None if there isn't one."""
    if amount is None or amount == "":
        return None
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    return value * 2080 if HOURLY_RATE.match((interval or "").strip()) else value


# Google states pay without a currency mark as often as with one, and names
# the interval in words: "84K–96K a year", "$40 an hour", "$5,000 a month".
# parse_salary() anchors on a "$" that is not reliably there, and making it
# optional there would read "5 years" out of any job body as a salary — so
# Google's phrasing gets its own reader.
GOOGLE_PAY = re.compile(
    r"\$?\s*([\d,.]+)\s*([km])?\s*(?:[-–—]|to)?\s*"
    r"(?:\$?\s*([\d,.]+)\s*([km])?)?\s*(?:an?|per)\s+"
    r"(hour|year|month|week)", re.I)

PAY_PERIODS = {"hour": 2080, "week": 52, "month": 12, "year": 1}


def google_salary(text):
    """Read Google's pay phrasing into yearly dollars, or (None, None)."""
    m = GOOGLE_PAY.search(text or "")
    if not m:
        return None, None

    def num(raw, suffix):
        if not raw:
            return None
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            return None
        return value * {"k": 1000, "m": 1000000}.get((suffix or "").lower(), 1)

    per = PAY_PERIODS[m.group(5).lower()]
    lo, hi = num(m.group(1), m.group(2)), num(m.group(3), m.group(4))
    return (lo * per if lo else None), (hi * per if hi else None)
