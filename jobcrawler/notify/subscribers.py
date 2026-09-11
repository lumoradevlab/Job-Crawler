"""Who the bot sends to, and what each of them asked for.

The bot sent to one chat id read from the environment. Everything else about
it was already shaped for many readers — the notifier binds a chat id once
and threads it cleanly — so opening it up is mostly a matter of having a list
to iterate and somewhere to keep each reader's answers.

Three things are per person and cannot be shared:

    roles, country   what they asked for
    seen             what they have already been sent
    muted, applied   what they have said about it

`seen` is the one that needs care. It grows on every run and a subscriber of
a year would carry thousands of URLs, so it holds hashes rather than links
and is pruned against the longest date window any run uses. A posting old
enough to fall outside that window cannot be re-sent whatever we remember
about it, so keeping its key buys nothing.

Deliberately not stored: names, usernames, message text, or anything about a
posting beyond what the tracker needs. The moment a stranger's job search
lives on someone else's infrastructure it is that person's responsibility,
and the cheapest way to hold it responsibly is to hold almost none of it.
"""

import hashlib
import json
from datetime import datetime, timedelta

from ..filters.countries import DEFAULT_COUNTRY
from ..roles import DEFAULT_ROLE

# Keys older than this are dropped from a subscriber's seen list. It has to
# exceed the widest --days any run uses, or a pruned posting comes back as
# new; 120 is comfortably past the 60-day default sweep.
SEEN_TTL_DAYS = 120


def seen_key(url):
    """A short stable handle for a posting, so seen lists stay small."""
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:12]


class Subscriber:
    """One reader, and everything the bot knows about them."""

    __slots__ = ("chat_id", "roles", "country", "joined", "paused",
                 "seen", "muted", "applied", "saved")

    # A sentinel, because an empty roles list is a real state — it is what
    # someone has mid-signup, having untoggled everything — and `or` would
    # silently replace it with the default.
    _UNSET = object()

    def __init__(self, chat_id, roles=_UNSET, country=DEFAULT_COUNTRY,
                 joined=None, paused=False, seen=None, muted=None,
                 applied=None, saved=None):
        self.chat_id = str(chat_id)
        self.roles = ([DEFAULT_ROLE] if roles is Subscriber._UNSET
                      else list(roles or []))
        self.country = country or DEFAULT_COUNTRY
        self.joined = joined or datetime.now().strftime("%Y-%m-%d")
        self.paused = bool(paused)
        # {key: "YYYY-MM-DD"} — dated so it can be pruned.
        self.seen = dict(seen or {})
        self.muted = list(muted or [])
        self.applied = dict(applied or {})
        self.saved = dict(saved or {})

    # -- what they asked for ----------------------------------------------
    def wants(self, company):
        """False when this subscriber has muted the company."""
        name = (company or "").strip().lower()
        return name not in {m.strip().lower() for m in self.muted}

    def mute(self, company):
        if (company or "").strip() and self.wants(company):
            self.muted.append(company.strip())
            return True
        return False

    # -- what they have been sent -----------------------------------------
    def has_seen(self, url):
        return seen_key(url) in self.seen

    def mark_seen(self, url, today):
        self.seen[seen_key(url)] = today

    def prune(self, today=None):
        """Drop seen keys older than the window any run could still ask for.

        Returns how many were dropped. A posting that has aged out cannot be
        re-sent whatever we remember, so this is free — and without it the
        file grows forever.
        """
        try:
            cutoff = (datetime.strptime(today or datetime.now().strftime("%Y-%m-%d"),
                                        "%Y-%m-%d")
                      - timedelta(days=SEEN_TTL_DAYS)).strftime("%Y-%m-%d")
        except ValueError:
            return 0
        stale = [k for k, d in self.seen.items() if d < cutoff]
        for k in stale:
            del self.seen[k]
        return len(stale)

    # -- serialisation ------------------------------------------------------
    def as_record(self):
        return {"roles": self.roles, "country": self.country,
                "joined": self.joined, "paused": self.paused,
                "seen": self.seen, "muted": self.muted,
                "applied": self.applied, "saved": self.saved}

    @classmethod
    def from_record(cls, chat_id, data):
        d = data or {}
        return cls(chat_id, roles=d.get("roles"), country=d.get("country"),
                   joined=d.get("joined"), paused=d.get("paused"),
                   seen=d.get("seen"), muted=d.get("muted"),
                   applied=d.get("applied"), saved=d.get("saved"))


class Subscribers:
    """The subscriber file, loaded once and written when it changes."""

    def __init__(self, path):
        self.path = path
        self.people = {}

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        self.people = {cid: Subscriber.from_record(cid, rec)
                       for cid, rec in (data or {}).items()
                       if isinstance(rec, dict)}
        return self

    def save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({cid: s.as_record() for cid, s in self.people.items()},
                      fh, indent=1, ensure_ascii=False)

    # -- membership ---------------------------------------------------------
    def get(self, chat_id):
        return self.people.get(str(chat_id))

    def add(self, chat_id, **over):
        """Add a subscriber, or return the existing one unchanged.

        Idempotent because /start is a button people press twice: the second
        press must not reset the roles they have since chosen.
        """
        cid = str(chat_id)
        if cid not in self.people:
            self.people[cid] = Subscriber(cid, **over)
        return self.people[cid]

    def remove(self, chat_id):
        """Forget someone entirely. /stop, or a 403 from Telegram.

        A block is the same event as an unsubscribe and has to be treated as
        one: Telegram returns 403 for a blocked chat forever, so a bot that
        keeps the row wastes one request per run per departed reader, for as
        long as it runs.
        """
        return self.people.pop(str(chat_id), None) is not None

    def active(self):
        """Everyone who should receive this run."""
        return [s for s in self.people.values() if not s.paused]

    def __len__(self):
        return len(self.people)
