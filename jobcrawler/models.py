"""The one record every source normalises into."""


def row(source, title, company, location, url, posted="", remote=True,
        us=None, description="", match_text=None, **extra):
    """Every source returns these keys, so one gate can judge them all."""
    d = {
        "source": source,
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "url": url or "",
        "posted": posted or "",
        "remote": remote,
        "us": us,
        "description": (description or "")[:2000],
        "match_text": match_text,
        "easy_apply": "?",
        "apply_url": "",
        "query": "",
        # Only a few sources state pay; the rest leave these empty.
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_predicted": "",
    }
    d.update(extra)
    return d
