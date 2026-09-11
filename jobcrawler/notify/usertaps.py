"""Applying a tap to the subscriber who made it.

The single-reader version resolved a tap against one state file. With many
readers the same button means different things to different people — one
reader muting Acme must not silence it for anyone else — so a tap resolves
to a subscriber first and a posting second.

Telegram makes the first half free: every callback carries the chat it came
from. The second half is what the sent record exists for. A button carries
"applied:<digest>" and nothing else, so the posting has to have been stored
under that digest when it was sent — which is why seen_key and tap_id are
now one function rather than two.
"""

from .actions import MARKS, decode
from .subscribers import seen_key


def apply_tap(sub, action, key, today):
    """Record one tap against one subscriber. Returns their sent record.

    Idempotent, like the single-reader version and for the same reason: a
    batch may be re-read after a crash between writing and acknowledging,
    so every branch assigns rather than accumulates.
    """
    record = sub.sent.get(key)
    if record is None:
        return None

    if action == "opening":
        # A question, not a verdict — see the apply flow in actions.py. The
        # posting is untouched until the reader answers it.
        record["asked_at"] = today
        return record
    if action == "cancel":
        record.pop("asked_at", None)
        return record

    record["state"] = action
    record["acted_at"] = today
    url = record.get("url") or ""

    if action == "applied":
        sub.applied[url] = {"title": record.get("title", ""),
                            "company": record.get("company", ""),
                            "when": today}
        sub.saved.pop(url, None)
    elif action == "saved":
        sub.saved[url] = {"title": record.get("title", ""),
                          "company": record.get("company", ""),
                          "when": today}
    elif action == "muted":
        sub.mute(record.get("company", ""))
    return record


def handle_tap(bot, sub, query, today):
    """Answer and apply one callback from one subscriber.

    Returns True when the tap was a job action. Setup taps — the role and
    country pickers — are routed elsewhere and are not this function's
    business.
    """
    action, key = decode(query.get("data") or "")
    if not action:
        return False

    # Answered first: the toast is what the reader sees, and its id expires
    # about a minute after the tap. On a batch run most are already dead,
    # which answer() treats as ordinary rather than as a failure.
    bot.answer(query.get("id"), action)

    record = apply_tap(sub, action, key, today)
    if record is None:
        # A posting old enough to have been pruned. The reader still got
        # their toast; there is simply nothing left to record.
        return True

    message_id = record.get("message_id") or (
        (query.get("message") or {}).get("message_id"))
    if not message_id:
        return True

    if action == "opening":
        bot.ask_confirm_in(sub.chat_id, message_id, record.get("url", ""))
    elif action == "cancel":
        bot.restore_buttons_in(sub.chat_id, message_id, record.get("url", ""))
    elif MARKS.get(action):
        text = (query.get("message") or {}).get("text") or record.get("title", "")
        bot.mark_in(sub.chat_id, message_id, _esc(text), action)
    return True


def _esc(text):
    """Telegram hands a tapped message back as plain text, not the HTML it
    was sent as. Re-escaping is what stops an '&' in a company name from
    breaking the edit that follows."""
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))
