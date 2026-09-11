"""Posting new jobs to a Telegram chat.

Stdlib only, like the rest of the crawler: the Bot API is HTTP and JSON, so
python-telegram-bot would add a dependency tree to save nothing. The Fetcher
already retries and counts, so it is reused rather than a second HTTP client
being written here.

Two things about the Bot API shape the code. Messages are rate-limited to
about 30 a second overall and roughly 20 a minute to one chat, and exceeding
it earns a 429 carrying retry_after — so sends are paced and a 429 is obeyed
rather than retried blindly. And a send can fail for reasons a retry will
never fix (a wrong chat id, a bot never started by the user), so those are
reported once, clearly, instead of being retried into a timeout.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .actions import MARKS, TOASTS, keyboard

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram hard-limits a message to 4096 characters. Nothing here comes close
# unless a description runs long, and a truncated posting is still useful.
MAX_MESSAGE = 4096

# Roughly 20 messages a minute to one chat is the documented ceiling; 3.5s
# keeps a burst of new postings comfortably under it without making a
# ten-job morning feel slow.
SEND_GAP = 3.5


class TelegramError(Exception):
    """A send that failed for a reason retrying will not fix."""


def _esc(text):
    """Escape for parse_mode=HTML — the only three characters it reserves.

    HTML is used rather than MarkdownV2 because job titles are full of the
    characters MarkdownV2 reserves — '(', ')', '-', '.', '+' — and escaping
    eighteen of them correctly is a bug farm. HTML reserves three.
    """
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _salary(job):
    """The pay line, or None when the posting states none worth trusting.

    A predicted salary is a model's guess rather than the posting's number —
    the crawler already refuses to record it as fact, and a bot that renders
    a guess as "$180,000" is asserting something the employer never said.
    """
    if getattr(job, "salary_predicted", "") == "yes":
        return None
    lo, hi = job.salary_min, job.salary_max
    if not lo and not hi:
        return None
    unit = (job.salary_currency or "USD").upper()
    money = lambda v: f"{int(round(float(v))):,}"
    if lo and hi and lo != hi:
        return f"{money(lo)}–{money(hi)} {unit}"
    return f"{money(lo or hi)} {unit}"


def format_posting(job, country=None):
    """One posting as a Telegram HTML message.

    The title links to the posting, so the message is actionable from the
    notification itself — which is the entire point of a job bot. Everything
    below the title is optional and omitted when the source did not state it,
    rather than being rendered as an empty field.
    """
    title = _esc(job.title or "Untitled role")
    head = f'<a href="{_esc(job.url)}">{title}</a>' if job.url else f"<b>{title}</b>"
    # Only when a run says which country it is for. One feed needs no flag;
    # two in the same chat do, because a worldwide posting names no country
    # in its location and would otherwise read the same in both.
    flag = getattr(country, "flag", "") if country else ""
    lines = [f"<b>{_esc(job.company)}</b>" if job.company else "", head]

    where = job.location or ("Remote" if job.remote else "")
    facts = []
    if where:
        facts.append("📍 " + _esc(where))
    pay = _salary(job)
    if pay:
        facts.append("💰 " + _esc(pay))
    if job.posted:
        facts.append("🗓 " + _esc(job.posted[:10]))
    if facts:
        lines.append(" · ".join(facts))

    # The source is worth stating: an ATS link is the company's own posting
    # and outlives the aggregator copy, and knowing which is which is how a
    # reader decides whether to trust the location.
    lines.append(f"<i>via {_esc(job.source)}</i>" + (f" {flag}" if flag else ""))

    text = "\n".join(x for x in lines if x)
    return text[:MAX_MESSAGE]


class TelegramNotifier:
    """Sends postings to one chat. Built from env vars, never from a file."""

    def __init__(self, token, chat_id, fetch=None, report=None, gap=SEND_GAP):
        if not token or not chat_id:
            raise TelegramError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set")
        self.token = token
        self.chat_id = str(chat_id)
        self.report = report
        self.gap = gap
        self._last_send = 0.0

    # -- transport ---------------------------------------------------------
    def _call(self, method, payload):
        """One Bot API call. Returns the 'result', or raises TelegramError."""
        url = API.format(token=self.token, method=method)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8"))
            except Exception:
                pass
            if e.code == 429:
                # The API says exactly how long to wait; guessing is how a
                # bot turns one throttle into a cascade of them.
                wait = 5
                if isinstance(detail, dict):
                    wait = (detail.get("parameters") or {}).get("retry_after", 5)
                raise _Throttled(wait)
            what = detail.get("description") if isinstance(detail, dict) else e.reason
            raise TelegramError(f"{method} failed: HTTP {e.code} — {what}")
        except urllib.error.URLError as e:
            raise TelegramError(f"{method} failed: {e.reason}")
        if not data.get("ok"):
            raise TelegramError(f"{method} failed: {data.get('description')}")
        return data.get("result")

    def _pace(self):
        """Hold the per-chat rate limit without sleeping when not needed."""
        elapsed = time.monotonic() - self._last_send
        if self._last_send and elapsed < self.gap:
            time.sleep(self.gap - elapsed)
        self._last_send = time.monotonic()

    # -- api ---------------------------------------------------------------
    def check(self):
        """Confirm the token works and return the bot's username.

        Called before a run sends anything, so a bad token is one clear line
        at the start rather than a failure per posting.
        """
        me = self._call("getMe", {}) or {}
        return me.get("username") or "?"

    def send(self, text, preview=False, markup=None):
        """Send one HTML message, obeying a 429's retry_after once."""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview,
        }
        if markup:
            payload["reply_markup"] = markup
        self._pace()
        try:
            return self._call("sendMessage", payload)
        except _Throttled as t:
            if self.report:
                self.report.warn(f"  ! telegram: throttled, waiting {t.wait}s")
            time.sleep(t.wait + 1)
            self._last_send = time.monotonic()
            return self._call("sendMessage", payload)

    def send_postings(self, jobs, buttons=True, key_of=None, country=None):
        """Send one message per posting. Returns {job_key: message_id}.

        The message_id is the whole reason this returns a mapping rather than
        a count. Editing a message after its button is tapped needs the id
        Telegram assigned when it was sent, and that id exists nowhere else —
        so a send that does not capture it cannot be followed up, and the
        buttons would have nothing to rewrite.

        One posting failing must not silence the rest: a single unsendable
        title (or a transient 5xx on one call) is worth a warning, not the
        loss of the other nine jobs found that morning.
        """
        sent = {}
        for job in jobs:
            key = key_of(job) if key_of else job.url
            try:
                result = self.send(format_posting(job, country),
                                   markup=keyboard(key) if buttons else None)
                if isinstance(result, dict) and result.get("message_id"):
                    sent[key] = result["message_id"]
            except TelegramError as e:
                if self.report:
                    self.report.warn(f"  ! telegram: {job.title[:40]!r}: {e}")
        return sent

    # -- receiving ---------------------------------------------------------
    def updates(self, offset=None, limit=100):
        """Every update waiting, oldest first.

        Telegram holds an undelivered update for 24 hours and then drops it,
        so a schedule that stops running for a day loses whatever was tapped
        in the meantime. Nothing here can prevent that; what it can do is not
        make it worse, which is why acknowledging is a separate step — see
        acknowledge().
        """
        payload = {"timeout": 0, "limit": limit,
                   "allowed_updates": ["callback_query", "message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload) or []

    def acknowledge(self, update_id):
        """Tell Telegram a batch is handled, so it is not served again.

        getUpdates with an offset one past the last id is the only way to
        confirm receipt. Until that call the same taps come back every run —
        which for a mute is not merely noisy but wrong, since re-applying it
        would undo a later unmute.
        """
        self._call("getUpdates", {"offset": update_id + 1, "limit": 1,
                                  "timeout": 0})

    def answer(self, callback_id, action):
        """The toast on the button itself, within about a second.

        This is what makes a twelve-hour batch delay tolerable: the tap is
        acknowledged now and only its consequence waits. A callback left
        unanswered shows the reader a spinner until it times out, which reads
        as a broken bot.
        """
        try:
            self._call("answerCallbackQuery",
                       {"callback_query_id": callback_id,
                        "text": TOASTS.get(action, "Done")})
        except TelegramError:
            # A callback id expires after about a minute. On a batch run most
            # of them are long dead, and that is expected rather than an
            # error — the message edit below is what the reader actually sees.
            pass

    def mark(self, message_id, text, action):
        """Rewrite a tapped message to show what was done, and drop its buttons.

        Rewritten rather than deleted: the posting is still the thing worth
        looking at, and a feed that erases what you touched cannot be
        reviewed. Dropping the keyboard is what stops a second tap on a
        decision already made.
        """
        mark = MARKS.get(action)
        body = f"{text}\n\n<b>{mark}</b>" if mark else text
        try:
            self._call("editMessageText", {
                "chat_id": self.chat_id, "message_id": message_id,
                "text": body[:MAX_MESSAGE], "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            return True
        except TelegramError as e:
            # "message is not modified" and "message to edit not found" are
            # both ordinary here — a message deleted by the reader, or a tap
            # already applied by an earlier run.
            if self.report:
                self.report.warn(f"  ! telegram: could not mark {message_id}: {e}")
            return False


def format_digest(jobs, shown):
    """The overflow: everything past the individual messages, in one message.

    The flood guard was written when a run turned up a handful of Android
    jobs and forty meant the state file had been lost. With fourteen sources
    and seventeen role profiles, forty is an ordinary Tuesday — so refusing
    to send became a failure on healthy runs, and sending only the newest
    quietly dropped the rest.

    A digest keeps both properties: the newest are worth a message each,
    with buttons; the tail is worth a line each, with a link. Nothing is
    lost and nothing floods.
    """
    lines = [f"<b>+{len(jobs)} more</b> — newest {shown} sent above", ""]
    for job in jobs:
        title = _esc(job.title or "Untitled role")[:70]
        link = f'<a href="{_esc(job.url)}">{title}</a>' if job.url else title
        where = job.company or job.location or ""
        lines.append("· " + link + (f" — {_esc(where)}" if where else ""))

    text = "\n".join(lines)
    if len(text) <= MAX_MESSAGE:
        return text
    # A very wide run can overflow one message too. Trim by line rather than
    # by character, so the last entry is whole instead of cut mid-link.
    while len(text) > MAX_MESSAGE - 40 and len(lines) > 3:
        lines.pop()
        text = "\n".join(lines)
    return text + "\n\n<i>…list truncated</i>"


class _Throttled(Exception):
    """A 429 carrying the API's own retry_after."""

    def __init__(self, wait):
        super().__init__(f"retry after {wait}s")
        self.wait = wait
