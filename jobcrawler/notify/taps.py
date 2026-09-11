"""Draining the taps that arrived since the last run, and applying them.

This is the receiving half of a bot that previously only spoke. It runs at
the start of a scheduled run, before the crawl, for one reason worth stating:
a company muted this morning must not reappear in this morning's results.
Applying taps afterwards would send the very postings the reader just asked
never to see again.

The ordering inside is equally deliberate. State is written before the batch
is acknowledged. Acknowledged first, a crash in between would lose the taps
for good — Telegram never serves an acknowledged update again. Written first,
the worst case is re-reading a batch already applied, and every action here
is idempotent precisely so that re-reading costs nothing: marking an applied
job applied, or muting a muted company, both land on the state they already
describe.
"""

from .actions import decode, is_open, tap_id

META = "_meta"


def index_by_tap(state):
    """{tap_id: job_key} for everything the state knows about.

    A button carries a digest rather than a URL because Telegram caps
    callback data at 64 bytes — see actions.py. Turning that digest back into
    a job means indexing what we have, which is one pass over the state file.
    """
    return {tap_id(k): k for k in state if not k.startswith("_")}


def muted_companies(state):
    """The companies the reader has silenced, lowercased for comparison."""
    meta = state.get(META) or {}
    return {c.strip().lower() for c in (meta.get("muted_companies") or [])
            if c and c.strip()}


def mute(state, company):
    """Add a company to the mute list, keeping it a set in list clothing."""
    if not (company or "").strip():
        return False
    meta = state.setdefault(META, {})
    muted = meta.setdefault("muted_companies", [])
    if company.strip().lower() in {m.strip().lower() for m in muted}:
        return False
    muted.append(company.strip())
    return True


def apply_tap(state, action, job_key, today):
    """Record one tap. Returns the entry it touched, or None if unknown.

    Idempotent by construction: every branch assigns rather than accumulates,
    so applying the same tap twice leaves the same state. That is what makes
    it safe to write before acknowledging — see the module docstring.
    """
    entry = state.get(job_key)
    if entry is None:
        return None
    if action == "opening":
        # A question, not a verdict. The job is untouched until it is
        # answered — marking it here is exactly the bug this flow exists to
        # avoid, where opening a page counts as having applied.
        entry["asked_at"] = today
        return entry
    if action == "cancel":
        entry.pop("asked_at", None)
        return entry
    entry["state"] = action
    entry["acted_at"] = today
    if action == "muted":
        mute(state, entry.get("company", ""))
    return entry


def drain(notifier, state, today, report=None):
    """Read every waiting tap, apply it, and acknowledge the batch.

    Returns (applied, seen) — how many taps landed on a known job, and how
    many updates were read in total. The two differ when a reader taps a
    posting old enough to have been pruned from the state, which is ordinary
    rather than an error.
    """
    meta = state.setdefault(META, {})
    offset = meta.get("last_update_id")
    offset = offset + 1 if isinstance(offset, int) else None

    try:
        batch = notifier.updates(offset=offset)
    except Exception as e:                     # a tap outage is not a crawl outage
        if report:
            report.warn(f"  ! telegram: could not read taps: {e}")
        return 0, 0

    if not batch:
        return 0, 0

    by_tap = index_by_tap(state)
    applied, last_id = 0, None

    for update in batch:
        last_id = update.get("update_id", last_id)
        query = update.get("callback_query")
        if not query:
            message = update.get("message") or {}
            handle_command(notifier, state, message.get("text"))
            continue
        action, tap = decode((query.get("data") or ""))
        if not action:
            continue

        # Answer first: the toast is what the reader sees, and it expires
        # about a minute after the tap. On a batch run most are already dead,
        # which answer() treats as ordinary.
        notifier.answer(query.get("id"), action)

        job_key = by_tap.get(tap)
        if not job_key:
            continue
        entry = apply_tap(state, action, job_key, today)
        if entry is None:
            continue
        if action not in ("opening", "cancel"):
            applied += 1

        message_id = entry.get("message_id")
        if not message_id:
            continue

        if action == "opening":
            # The url button beside this one has already opened the posting;
            # this only turns the message into the yes/no prompt.
            notifier.ask_confirm(message_id, job_key)
        elif action == "cancel":
            notifier.restore_buttons(message_id, job_key,
                                     entry.get("apply_url"))
        else:
            text = (query.get("message") or {}).get("text") or entry.get("title", "")
            notifier.mark(message_id, _as_html(text), action)

    if last_id is not None:
        # State is already updated; acknowledging now is what makes the
        # write-then-acknowledge ordering hold.
        meta["last_update_id"] = last_id
        notifier.acknowledge(last_id)

    if report and applied:
        report.line(f"{applied} tap{'' if applied == 1 else 's'} applied")
    return applied, len(batch)


# The commands a reader can type. Answered in the same batch pass as the
# taps, because a bot that reads its updates but ignores half of them is a
# bot that silently swallows what you typed.
COMMANDS = {
    "/applied": ("applied", "Jobs you have applied to"),
    "/saved": ("saved", "Jobs you have saved"),
    "/muted": (None, "Companies you have muted"),
    "/help": (None, "What this bot does"),
}

HELP = (
    "<b>Job bot</b>\n"
    "New remote roles, twice a day. Tap a button under any job:\n\n"
    "✅ <b>Applied</b> — mark it, keeps it in /applied\n"
    "🔖 <b>Save</b> — keeps it in /saved\n"
    "🚫 <b>Not for me</b> — never send this one again\n"
    "🔕 <b>Mute company</b> — never send anything from them again\n\n"
    "Commands: /applied /saved /muted /help\n\n"
    "<i>Taps are read at the next scheduled run, so a mute takes effect "
    "from the next batch of jobs.</i>"
)


def listing(state, want, title):
    """A summary message for /applied or /saved."""
    rows = [(k, v) for k, v in state.items()
            if not k.startswith("_") and isinstance(v, dict)
            and v.get("state") == want]
    rows.sort(key=lambda kv: kv[1].get("acted_at", ""), reverse=True)
    if not rows:
        return f"<b>{title}</b>\n\nNothing here yet."
    lines = [f"<b>{title}</b> ({len(rows)})", ""]
    for key, entry in rows[:30]:
        when = entry.get("acted_at", "")
        name = _as_html(entry.get("title", "?"))
        firm = _as_html(entry.get("company", ""))
        lines.append(f'· <a href="{_as_html(key)}">{name}</a>'
                     + (f" — {firm}" if firm else "")
                     + (f" <i>{when}</i>" if when else ""))
    if len(rows) > 30:
        lines.append(f"\n<i>…and {len(rows) - 30} more</i>")
    return "\n".join(lines)


def muted_listing(state):
    names = sorted((state.get(META) or {}).get("muted_companies") or [])
    if not names:
        return "<b>Muted companies</b>\n\nNone."
    return ("<b>Muted companies</b> (%d)\n\n" % len(names)
            + "\n".join("· " + _as_html(n) for n in names))


def handle_command(notifier, state, text):
    """Answer one typed command. Returns True if it was one."""
    word = (text or "").strip().split()[0].lower() if (text or "").strip() else ""
    if word not in COMMANDS:
        return False
    want, title = COMMANDS[word]
    if word == "/help":
        body = HELP
    elif word == "/muted":
        body = muted_listing(state)
    else:
        body = listing(state, want, title)
    try:
        notifier.send(body)
    except Exception:
        pass
    return True


def _as_html(text):
    """Telegram hands back a tapped message as plain text, not as the HTML
    that was sent. Re-escaping is what stops an '&' in a company name from
    breaking the edit that follows."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def suppressed(state):
    """Job keys the reader has turned down, so they are never sent again."""
    return {k for k, v in state.items()
            if not k.startswith("_") and isinstance(v, dict)
            and v.get("state") in ("rejected", "muted")}
