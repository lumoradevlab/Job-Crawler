"""Turning one batch of updates into changes to the right subscriber.

The single-user drain read every update against one state file. With many
readers the same updates have to be sorted by who sent them, which Telegram
makes easy: every update carries the chat it came from, and that chat id is
the subscriber key.

What this does not do is decide what a tap *means* — that is actions.py and
taps.py, unchanged. This only answers "whose?" and "what did they type?".
"""

from ..filters.countries import COUNTRIES
from ..roles import PROFILES
from .actions import decode
from .onboarding import (COUNTRY_PROMPT, HELP, WELCOME, confirmation,
                         country_keyboard, role_keyboard, settings_text)


def chat_of(update):
    """The chat an update came from, whatever kind of update it is."""
    for key in ("message", "edited_message", "my_chat_member"):
        chat = (update.get(key) or {}).get("chat") or {}
        if chat.get("id"):
            return str(chat["id"])
    query = update.get("callback_query") or {}
    chat = ((query.get("message") or {}).get("chat") or {})
    if chat.get("id"):
        return str(chat["id"])
    # A callback whose message has aged out still names its sender.
    return str((query.get("from") or {}).get("id") or "") or None


def is_block(update):
    """True when this update says the reader has blocked or left the bot.

    Telegram reports it as a my_chat_member change to "kicked". Acting on it
    is what stops a departed reader costing a request every run forever.
    """
    change = update.get("my_chat_member") or {}
    status = ((change.get("new_chat_member") or {}).get("status") or "")
    return status in ("kicked", "left")


def handle_command(bot, subs, chat_id, text):
    """Answer a typed command. Returns True if it was one.

    Only /start may create a subscriber; every other command on an unknown
    chat is answered with the welcome, because someone typing /settings at a
    bot they have never started is asking to start it.
    """
    word = (text or "").strip().split()[0].lower() if (text or "").strip() else ""
    if not word.startswith("/"):
        return False
    word = word.split("@")[0]          # /help@thebot, from a group

    sub = subs.get(chat_id)
    if word == "/start":
        sub = subs.add(chat_id)
        bot.send_to(chat_id, WELCOME, markup=role_keyboard(sub.roles))
        return True

    if sub is None:
        bot.send_to(chat_id, WELCOME, markup=role_keyboard())
        subs.add(chat_id)
        return True

    if word == "/settings":
        bot.send_to(chat_id, settings_text(sub),
                    markup=role_keyboard(sub.roles))
    elif word == "/help":
        bot.send_to(chat_id, HELP)
    elif word == "/pause":
        sub.paused = True
        bot.send_to(chat_id, "⏸ Paused. /resume when you want them back.")
    elif word == "/resume":
        sub.paused = False
        bot.send_to(chat_id, "▶️ Back on. Next batch at the next run.")
    elif word == "/stop":
        subs.remove(chat_id)
        bot.send_to(chat_id, "Unsubscribed, and everything about you is "
                             "deleted. /start to come back.")
    elif word in ("/applied", "/saved", "/muted"):
        bot.send_to(chat_id, _listing(sub, word))
    else:
        return False
    return True


def _listing(sub, word):
    """One of the three "what have I marked" replies."""
    if word == "/muted":
        if not sub.muted:
            return "<b>Muted companies</b>\n\nNone."
        return ("<b>Muted companies</b> (%d)\n\n" % len(sub.muted)
                + "\n".join("· " + _esc(m) for m in sorted(sub.muted)))

    rows = sub.applied if word == "/applied" else sub.saved
    title = "Jobs you applied to" if word == "/applied" else "Jobs you saved"
    if not rows:
        return f"<b>{title}</b>\n\nNothing here yet."
    lines = [f"<b>{title}</b> ({len(rows)})", ""]
    for url, info in sorted(rows.items(), key=lambda kv: str(kv[1]),
                            reverse=True)[:30]:
        if isinstance(info, dict):
            name, when = info.get("title", "?"), info.get("when", "")
        else:
            name, when = "?", str(info)
        lines.append(f'· <a href="{_esc(url)}">{_esc(name)}</a>'
                     + (f" <i>{when}</i>" if when else ""))
    if len(rows) > 30:
        lines.append(f"\n<i>…and {len(rows) - 30} more</i>")
    return "\n".join(lines)


def handle_setup(bot, subs, chat_id, data, callback_id=None):
    """A tap on a role or country button. Returns True if it was one."""
    kind, _, value = (data or "").partition(":")
    if kind not in ("role", "country"):
        return False
    sub = subs.get(chat_id) or subs.add(chat_id)

    if kind == "role":
        if value == "done":
            if not sub.roles:
                bot.answer_raw(callback_id, "Pick at least one first")
                return True
            bot.send_to(chat_id, COUNTRY_PROMPT,
                        markup=country_keyboard(sub.country))
            return True
        if value in PROFILES:
            # A toggle, so the same picker serves signup and /settings.
            if value in sub.roles:
                sub.roles.remove(value)
            else:
                sub.roles.append(value)
            bot.edit_markup(chat_id, _message_id(bot, chat_id),
                            role_keyboard(sub.roles))
        return True

    if value in COUNTRIES:
        sub.country = value
        # Redraw before confirming, exactly as the role picker does. Without
        # it the tick never appears and the old keyboard stays live, so a
        # reader who taps again sees nothing happen twice — which reads as a
        # broken bot even though the choice saved the first time.
        bot.edit_markup(chat_id, _message_id(bot, chat_id),
                        country_keyboard(sub.country))
        bot.send_to(chat_id, confirmation(sub))
    return True


def _message_id(bot, chat_id):
    """The message the last tap came from, so its keyboard can be redrawn."""
    return bot.last_message_id.get(str(chat_id))


def _esc(text):
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))
