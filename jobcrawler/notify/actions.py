"""What a tap means, and the state one leaves behind.

A job message carries four buttons. Tapping one is the only way this bot
ever learns anything about what the reader wants, so the vocabulary is kept
small and the meanings are kept sharp:

    applied     you sent an application. The job stays, marked and dated.
    saved       worth a second look. Marked, and listed by /saved.
    rejected    not for you. Never sent again, and the company is not muted.
    muted       nothing from this company again, this posting included.

The distinction between `rejected` and `muted` is the one that matters. A
single bad posting from a company you would otherwise work for is a
rejection; a company you never want to hear from again is a mute. Collapsing
the two would mean one tap on a badly-titled role silently costing you every
future opening there.

Callback data is where this gets constrained rather than designed. Telegram
caps callback_data at 64 bytes, and a job's identity here is its URL — which
is routinely longer than that on its own. So the button carries a short
digest of the URL, and the run looks the digest up. That is a requirement of
the API, not a preference, and it is why jobs carry a `tap_id`.
"""

import hashlib

# One action per button, in the order they appear under a message.
ACTIONS = ("applied", "saved", "rejected", "muted",
           # The apply flow. "opening" is the tap that opens the posting and
           # asks whether you went through with it; "cancel" is the answer
           # that you did not, and puts the original buttons back.
           "opening", "cancel")

LABELS = {
    "applied": "✅ I applied",
    "saved": "🔖 Save",
    "rejected": "🚫 Not for me",
    "muted": "🔕 Mute company",
    "opening": "🚀 Apply",
    "cancel": "← Didn't apply",
}

# What the message says about itself afterwards. A tapped message is rewritten
# rather than deleted: the posting is still the thing you want to look at, and
# a feed that deletes what you touched is a feed you cannot review.
MARKS = {
    "applied": "✅ Applied",
    "saved": "🔖 Saved",
    "rejected": "🚫 Not for me",
    "muted": "🔕 Muted",
    # "opening" never marks a message: it is a question, not a verdict.
    "cancel": None,
}

# The toast Telegram shows on the button itself, within about a second. This
# is the whole reason the twelve-hour batch delay is tolerable: the reader is
# answered now, and only the consequence waits.
TOASTS = {
    "applied": "Marked as applied",
    "saved": "Saved",
    "rejected": "Won't show this again",
    "muted": "Muted — no more from this company",
    "opening": "Opening — tell me if you apply",
    "cancel": "No problem",
}

# 10 hex characters of SHA-1 over the job key. Collision odds across a state
# file of a few thousand jobs are negligible (birthday bound puts a 1-in-a-
# million chance at well over 1e6 entries), and the whole callback payload —
# "muted:" plus the digest — is 16 bytes against Telegram's 64.
TAP_DIGEST = 10


def tap_id(job_key):
    """The short, stable handle a button carries for one job."""
    return hashlib.sha1(job_key.encode("utf-8")).hexdigest()[:TAP_DIGEST]


def encode(action, job_key):
    """The callback_data for one button. Never longer than 64 bytes."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    return f"{action}:{tap_id(job_key)}"


def decode(data):
    """(action, tap_id) from a callback payload, or (None, None).

    Anything unrecognised is ignored rather than raising: callback data can
    arrive from an older version of the bot whose buttons are still sitting
    in the reader's chat history, and a run must not die on one.
    """
    action, _, tap = (data or "").partition(":")
    if action in ACTIONS and tap:
        return action, tap
    return None, None


# Which buttons a message carries in its resting state. "applied" is not
# among them: applying starts by opening the posting, and a button claiming
# you applied before you have seen the page is how /applied fills up with
# jobs you only glanced at.
RESTING = ("saved", "rejected", "muted")


def keyboard(job_key, url=None):
    """The inline keyboard under one job message.

    A url button opens the posting in the reader's browser. Telegram sends
    *no callback* for one — it is a link, not an action — so the bot cannot
    learn the tap happened. That is why Apply is two buttons side by side:
    the url one opens the page, and the callback one beside it tells us you
    went, which is what turns the message into the confirm prompt.

    Splitting them is not a workaround for a missing feature. A single
    button cannot both open a page and report back, and a bot that assumed
    opening meant applying would fill /applied with jobs you merely peeked
    at, which is the one thing an application tracker must not do.
    """
    rows = []
    if url:
        rows.append([
            {"text": LABELS["opening"], "url": url},
            {"text": "✅ Applied?", "callback_data": encode("opening", job_key)},
        ])
    rows.append([{"text": LABELS[a], "callback_data": encode(a, job_key)}
                 for a in RESTING])
    return {"inline_keyboard": rows}


def confirm_keyboard(job_key):
    """What replaces the buttons once Apply has been opened.

    Only two answers, because only two are true at this point: you applied,
    or you did not. Leaving Save and Mute here would invite a third answer
    to a yes/no question and leave the message in a state that means nothing.
    """
    return {"inline_keyboard": [[
        {"text": LABELS["applied"], "callback_data": encode("applied", job_key)},
        {"text": LABELS["cancel"], "callback_data": encode("cancel", job_key)},
    ]]}


def is_open(entry):
    """True when a job has not been acted on — i.e. its buttons still apply."""
    return (entry or {}).get("state", "sent") == "sent"
