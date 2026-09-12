"""Who the bot sends to, and what each of them asked for.

The bot sent to one chat id read from the environment. Everything else about
it was already shaped for many readers — the notifier binds a chat id once
and threads it cleanly — so opening it up is mostly a matter of having a list
to iterate and somewhere to keep each reader's answers.

Three things are per person and cannot be shared:

    roles, countries  what they asked for
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

import json
from datetime import datetime, timedelta

from ..filters.countries import DEFAULT_COUNTRY
from ..roles import DEFAULT_ROLE
from .actions import tap_id

# Keys older than this are dropped from a subscriber's seen list. It has to
# exceed the widest --days any run uses, or a pruned posting comes back as
# new; 120 is comfortably past the 60-day default sweep.
SEEN_TTL_DAYS = 120


# The same digest the buttons carry. It has to be: a tap arrives as
# "applied:<digest>" and nothing else, so the only way back to the posting
# is to have stored it under that key. Two digest schemes for one URL — one
# for the seen list, another for the buttons — would make a tap unresolvable.
def seen_key(url):
    """A short stable handle for a posting, shared with actions.tap_id."""
    return tap_id(url or "")


def countries_of(record):
    """The countries in a stored row, whichever shape wrote it.

    Rows written while a subscriber could hold only one carry a "country"
    string. Reading it as a one-country list is the entire migration: the
    next save writes the new key, and nobody has to be asked again for an
    answer they already gave.
    """
    if record.get("countries") is not None:
        return record["countries"]
    single = record.get("country")
    return [single] if single else None


class Subscriber:
    """One reader, and everything the bot knows about them."""

    __slots__ = ("chat_id", "roles", "countries", "joined", "paused",
                 "seen", "muted", "applied", "saved", "sent")

    # A sentinel, because an empty roles list is a real state — it is what
    # someone has mid-signup, having untoggled everything — and `or` would
    # silently replace it with the default. Countries are the same shape and
    # for the same reason.
    _UNSET = object()

    def __init__(self, chat_id, roles=_UNSET, countries=_UNSET,
                 joined=None, paused=False, seen=None, muted=None,
                 applied=None, saved=None, sent=None):
        self.chat_id = str(chat_id)
        self.roles = ([DEFAULT_ROLE] if roles is Subscriber._UNSET
                      else list(roles or []))
        # Someone in Canada usually wants the US postings too — most remote
        # roles on these boards are US-first, and a worldwide posting names
        # no country and qualifies under both — so this is a list, ordered
        # as the picker presents it rather than as it was tapped.
        self.countries = ([DEFAULT_COUNTRY] if countries is Subscriber._UNSET
                          else list(countries or []))
        self.joined = joined or datetime.now().strftime("%Y-%m-%d")
        self.paused = bool(paused)
        # {key: "YYYY-MM-DD"} — dated so it can be pruned.
        self.seen = dict(seen or {})
        # {key: {title, company, url, message_id, state, ...}} for postings
        # this reader was actually sent. A tap carries a digest and nothing
        # else, so without this there is no way back to the job it names —
        # not the title to show in /applied, nor the company to mute, nor
        # the message to rewrite. Only sent postings are here; `seen` stays
        # the cheap dated index and this holds the few that need acting on.
        self.sent = dict(sent or {})
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

    def record_sent(self, job, message_id, today):
        """Remember enough about a sent posting to act on a tap about it."""
        key = seen_key(job.url)
        self.seen[key] = today
        self.sent[key] = {"title": job.title, "company": job.company,
                          "url": job.url, "message_id": message_id,
                          "state": "sent", "sent_at": today}
        return key

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
            # A posting nobody acted on is not worth keeping once it can no
            # longer be re-sent. One they applied to or saved is, so those
            # stay however old they get — /applied is a record, not a feed.
            if self.sent.get(k, {}).get("state") in (None, "sent", "rejected"):
                self.sent.pop(k, None)
        return len(stale)

    # -- serialisation ------------------------------------------------------
    def as_record(self):
        return {"roles": self.roles, "countries": self.countries,
                "joined": self.joined, "paused": self.paused,
                "seen": self.seen, "sent": self.sent, "muted": self.muted,
                "applied": self.applied, "saved": self.saved}

    @classmethod
    def from_record(cls, chat_id, data):
        d = data or {}
        return cls(chat_id, roles=d.get("roles"), countries=countries_of(d),
                   joined=d.get("joined"), paused=d.get("paused"),
                   seen=d.get("seen"), sent=d.get("sent"),
                   muted=d.get("muted"), applied=d.get("applied"),
                   saved=d.get("saved"))


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
