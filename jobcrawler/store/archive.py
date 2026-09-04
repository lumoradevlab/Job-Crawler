"""The append-only record of every posting the crawler has ever matched.

Without it the full record exists nowhere. write_outputs() rewrites its three
files from scratch every run and — unless --include-seen — is handed only the
*new* postings, so yesterday's CSV is gone; and the seen-state keeps a title,
a company and a date, not a location, salary or link.

One line per posting, appended and never rewritten, so an interrupted run can
at worst lose its last line rather than the file.
"""

import json

from ..models import Posting
from .seen import job_key


class Archive:
    """The archive file, with its key index read at most once per run.

    Deciding what is new means knowing every key already stored, and the
    function this replaces re-read and re-parsed the whole file to find out —
    every run, growing with the archive. Holding the index on the instance
    bounds that to one pass however many times a run asks.

    It is still one pass: the alternative is a sidecar index file, which buys
    O(1) startup and pays for it with two files that can disagree after a
    killed run. For a file that grows by a handful of lines a day, a single
    scan is the better trade — and the cost is now stated in one place rather
    than hidden inside an append.
    """

    def __init__(self, path):
        self.path = path
        self._keys = None

    def postings(self):
        """Every posting ever matched. A missing file is empty."""
        out = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(Posting.from_record(json.loads(line)))
                    except json.JSONDecodeError:
                        continue    # a half-written line from a killed run
        except FileNotFoundError:
            pass
        return out

    def keys(self):
        """Every key in the archive, read once and kept for the run."""
        if self._keys is None:
            self._keys = {job_key(j) for j in self.postings()}
        return self._keys

    def add(self, jobs):
        """Append whatever this run matched that is not already stored."""
        known = self.keys()
        added = [j for j in jobs if job_key(j) not in known]
        if added:
            with open(self.path, "a", encoding="utf-8") as fh:
                for j in added:
                    fh.write(json.dumps(j.as_record(), ensure_ascii=False)
                             + "\n")
            # The index stays true without another pass over the file.
            known.update(job_key(j) for j in added)
        return len(added)


def load_archive(path):
    """Every posting the crawler has ever matched. A missing file is empty."""
    return Archive(path).postings()


def append_archive(path, jobs):
    """Add whatever this run matched that the archive has not seen before."""
    return Archive(path).add(jobs)
