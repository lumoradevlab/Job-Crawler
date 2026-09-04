"""The append-only record of every posting the crawler has ever matched."""

import json

from ..models import Posting
from .seen import job_key


def load_archive(path):
    """Every posting the crawler has ever matched. A missing file is empty."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Posting.from_record(json.loads(line)))
                except json.JSONDecodeError:
                    continue        # a half-written line from a killed run
    except FileNotFoundError:
        pass
    return out


def append_archive(path, jobs):
    """Add whatever this run matched that the archive has not seen before.

    Without this the full record exists nowhere. write_outputs() rewrites its
    three files from scratch every run and — unless --include-seen — is handed
    only the *new* postings, so yesterday's CSV is gone; and the seen-state
    keeps a title, a company and a date, not a location, salary or link. One
    line per posting, appended and never rewritten, so an interrupted run can
    at worst lose its last line rather than the file.
    """
    known = {job_key(j) for j in load_archive(path)}
    added = [j for j in jobs if job_key(j) not in known]
    if added:
        with open(path, "a", encoding="utf-8") as fh:
            for j in added:
                fh.write(json.dumps(j.as_record(), ensure_ascii=False)
                         + "\n")
    return len(added)
