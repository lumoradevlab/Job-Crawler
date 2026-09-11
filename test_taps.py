#!/usr/bin/env python3
"""Tests for the receiving half: buttons, taps, and the state they leave.

Offline like every other suite here — the notifier is a stub that records
calls, so nothing reaches api.telegram.org.

    python3 test_taps.py           # all of it
    python3 test_taps.py -v        # naming each case

Stdlib only, like the crawler itself.
"""

import unittest

import jobcrawler as c
from jobcrawler.notify import actions, taps


class StubBot:
    """A notifier that records instead of calling, and serves a fixed batch."""

    def __init__(self, batch=()):
        self.batch = list(batch)
        self.answered = []
        self.marked = []
        self.sent = []
        self.acked = []
        self.offsets = []

    def updates(self, offset=None, limit=100):
        self.offsets.append(offset)
        return self.batch

    def acknowledge(self, update_id):
        self.acked.append(update_id)

    def answer(self, callback_id, action):
        self.answered.append((callback_id, action))

    def mark(self, message_id, text, action):
        self.marked.append((message_id, action))
        return True

    def send(self, text, preview=False, markup=None):
        self.sent.append(text)
        return {"message_id": len(self.sent)}


def tap(job_key, action, update_id=1, message_id=99, cb="cb1"):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": cb,
            "data": actions.encode(action, job_key),
            "message": {"message_id": message_id, "text": "Android Engineer"},
        },
    }


def state_with(*keys, **over):
    st = {}
    for i, k in enumerate(keys):
        st[k] = {"first_seen": "2026-09-01", "title": f"Android Engineer {i}",
                 "company": over.get("company", f"Company{i}"),
                 "message_id": 100 + i, "state": "sent"}
    st["_meta"] = dict(over.get("meta") or {})
    return st


# ==========================================================================
# Callback data, which the API caps at 64 bytes
# ==========================================================================
class TestCallbackData(unittest.TestCase):

    def test_a_long_url_still_fits_in_the_limit(self):
        # A job's identity is its URL, routinely longer than 64 bytes on its
        # own — which is why the button carries a digest, not the URL.
        url = "https://job-boards.greenhouse.io/" + "x" * 300
        for action in actions.ACTIONS:
            data = actions.encode(action, url)
            self.assertLessEqual(len(data.encode("utf-8")), 64, action)

    def test_a_payload_round_trips(self):
        url = "https://x.example/1"
        action, tap_id = actions.decode(actions.encode("muted", url))
        self.assertEqual(action, "muted")
        self.assertEqual(tap_id, actions.tap_id(url))

    def test_unrecognised_data_is_ignored_not_raised(self):
        # Buttons from an older version of the bot are still sitting in the
        # reader's history; a run must not die on one.
        for junk in ("", "nonsense", "applied", ":", "bogus:abc"):
            self.assertEqual(actions.decode(junk), (None, None))

    def test_every_action_has_a_label_a_mark_and_a_toast(self):
        for a in actions.ACTIONS:
            self.assertIn(a, actions.LABELS)
            self.assertIn(a, actions.MARKS)
            self.assertIn(a, actions.TOASTS)

    def test_the_keyboard_is_two_rows_of_two(self):
        rows = actions.keyboard("https://x.example/1")["inline_keyboard"]
        self.assertEqual([len(r) for r in rows], [2, 2])


# ==========================================================================
# Applying one tap
# ==========================================================================
class TestApplyTap(unittest.TestCase):

    def test_a_tap_records_its_action_and_the_date(self):
        st = state_with("https://x/1")
        taps.apply_tap(st, "applied", "https://x/1", "2026-09-11")
        self.assertEqual(st["https://x/1"]["state"], "applied")
        self.assertEqual(st["https://x/1"]["acted_at"], "2026-09-11")

    def test_muting_records_the_company_too(self):
        st = state_with("https://x/1", company="Acme")
        taps.apply_tap(st, "muted", "https://x/1", "2026-09-11")
        self.assertEqual(taps.muted_companies(st), {"acme"})

    def test_applying_the_same_tap_twice_changes_nothing(self):
        # The whole reason state may be written before acknowledging: a
        # re-read batch must be harmless.
        st = state_with("https://x/1", company="Acme")
        for _ in range(3):
            taps.apply_tap(st, "muted", "https://x/1", "2026-09-11")
        self.assertEqual(st["_meta"]["muted_companies"], ["Acme"])

    def test_a_tap_on_an_unknown_job_is_survivable(self):
        self.assertIsNone(
            taps.apply_tap(state_with(), "applied", "https://gone/9", "2026-09-11"))

    def test_rejecting_does_not_mute_the_company(self):
        # A bad posting is not a bad employer. Collapsing the two would cost
        # every future opening there on one tap.
        st = state_with("https://x/1", company="Acme")
        taps.apply_tap(st, "rejected", "https://x/1", "2026-09-11")
        self.assertEqual(taps.muted_companies(st), set())


# ==========================================================================
# Draining a batch
# ==========================================================================
class TestDrain(unittest.TestCase):

    def test_a_tap_is_answered_applied_and_marked(self):
        st = state_with("https://x/1")
        bot = StubBot([tap("https://x/1", "applied")])
        applied, seen = taps.drain(bot, st, "2026-09-11")
        self.assertEqual((applied, seen), (1, 1))
        self.assertEqual(bot.answered, [("cb1", "applied")])
        self.assertEqual(bot.marked, [(100, "applied")])

    def test_the_batch_is_acknowledged_by_its_last_id(self):
        st = state_with("https://x/1")
        bot = StubBot([tap("https://x/1", "saved", update_id=7)])
        taps.drain(bot, st, "2026-09-11")
        self.assertEqual(bot.acked, [7])
        self.assertEqual(st["_meta"]["last_update_id"], 7)

    def test_the_next_run_asks_from_one_past_the_last(self):
        # Without the offset the same taps come back forever — and for a
        # mute that is not merely noisy, it would undo a later unmute.
        st = state_with("https://x/1", meta={"last_update_id": 7})
        bot = StubBot([])
        taps.drain(bot, st, "2026-09-11")
        self.assertEqual(bot.offsets, [8])

    def test_a_first_run_asks_with_no_offset(self):
        bot = StubBot([])
        taps.drain(bot, state_with(), "2026-09-11")
        self.assertEqual(bot.offsets, [None])

    def test_a_tap_on_a_forgotten_job_is_still_answered(self):
        # The reader gets their toast even when the posting has aged out of
        # the state file; only the bookkeeping is skipped.
        bot = StubBot([tap("https://gone/9", "applied")])
        applied, seen = taps.drain(bot, state_with(), "2026-09-11")
        self.assertEqual((applied, seen), (0, 1))
        self.assertEqual(len(bot.answered), 1)

    def test_a_telegram_outage_does_not_take_the_crawl_down(self):
        class Broken(StubBot):
            def updates(self, offset=None, limit=100):
                raise RuntimeError("network down")

        self.assertEqual(
            taps.drain(Broken(), state_with(), "2026-09-11",
                       c.NullReporter()), (0, 0))

    def test_several_taps_in_one_batch_all_land(self):
        st = state_with("https://x/1", "https://x/2", "https://x/3")
        bot = StubBot([tap("https://x/1", "applied", 1, cb="a"),
                       tap("https://x/2", "saved", 2, cb="b"),
                       tap("https://x/3", "rejected", 3, cb="c")])
        applied, _ = taps.drain(bot, st, "2026-09-11")
        self.assertEqual(applied, 3)
        self.assertEqual(st["https://x/2"]["state"], "saved")
        self.assertEqual(bot.acked, [3])


# ==========================================================================
# What the taps suppress afterwards
# ==========================================================================
class TestSuppression(unittest.TestCase):

    def test_rejected_and_muted_jobs_are_suppressed(self):
        st = state_with("https://x/1", "https://x/2", "https://x/3")
        st["https://x/1"]["state"] = "rejected"
        st["https://x/2"]["state"] = "muted"
        st["https://x/3"]["state"] = "applied"
        self.assertEqual(taps.suppressed(st), {"https://x/1", "https://x/2"})

    def test_an_applied_job_is_not_suppressed(self):
        # You applied to it; it is the last thing to hide from you.
        st = state_with("https://x/1")
        st["https://x/1"]["state"] = "applied"
        self.assertEqual(taps.suppressed(st), set())

    def test_muted_company_matching_ignores_case_and_padding(self):
        st = state_with("https://x/1", meta={"muted_companies": ["  ACME  "]})
        self.assertEqual(taps.muted_companies(st), {"acme"})


# ==========================================================================
# Typed commands
# ==========================================================================
class TestCommands(unittest.TestCase):

    def test_applied_lists_what_was_applied_to(self):
        st = state_with("https://x/1", "https://x/2")
        st["https://x/1"]["state"] = "applied"
        bot = StubBot()
        self.assertTrue(taps.handle_command(bot, st, "/applied"))
        self.assertIn("Android Engineer 0", bot.sent[0])
        self.assertNotIn("Android Engineer 1", bot.sent[0])

    def test_an_empty_list_says_so_rather_than_sending_nothing(self):
        bot = StubBot()
        taps.handle_command(bot, state_with(), "/saved")
        self.assertIn("Nothing here yet", bot.sent[0])

    def test_help_names_every_button(self):
        bot = StubBot()
        taps.handle_command(bot, state_with(), "/help")
        for label in ("Applied", "Save", "Not for me", "Mute company"):
            self.assertIn(label, bot.sent[0])

    def test_a_non_command_is_left_alone(self):
        bot = StubBot()
        self.assertFalse(taps.handle_command(bot, state_with(), "hello"))
        self.assertEqual(bot.sent, [])

    def test_a_command_is_answered_inside_a_drain(self):
        # A bot that reads its updates and ignores half of them silently
        # swallows what you typed.
        bot = StubBot([{"update_id": 4, "message": {"text": "/help"}}])
        taps.drain(bot, state_with(), "2026-09-11")
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.acked, [4])

    def test_company_names_are_escaped_in_a_listing(self):
        st = state_with("https://x/1", company="Acme & <b>Co</b>")
        st["https://x/1"]["state"] = "applied"
        bot = StubBot()
        taps.handle_command(bot, st, "/applied")
        self.assertIn("&amp;", bot.sent[0])
        self.assertNotIn("<b>Co</b>", bot.sent[0])


# ==========================================================================
# The run itself: taps must reach the crawl that follows them
# ==========================================================================
class TestRunIntegration(unittest.TestCase):
    """Driven through main(), because the ordering is the thing being tested.

    Taps are drained before the crawl so that a company muted this morning
    does not reappear in this morning's results. Draining afterwards would
    send exactly the postings the reader just asked never to see again, and
    only an end-to-end run can catch that.
    """

    def setUp(self):
        import os, tempfile
        import jobcrawler.notify.bot as bot
        self.bot = bot
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "run")
        self.sent = []
        self._sources = dict(bot.SOURCES)
        self._notifier = bot.TelegramNotifier
        self._env = dict(os.environ)

        made = [c.row("greenhouse", "Android Engineer", "Acme", "Remote - US",
                      "https://x.example/acme", "2026-09-10"),
                c.row("greenhouse", "Android Developer", "Globex",
                      "Remote - US", "https://x.example/globex", "2026-09-10")]
        bot.SOURCES.clear()
        bot.SOURCES["greenhouse"] = lambda cfg, ctx: list(made)

        outer = self

        class Stub(StubBot):
            def __init__(self, *a, **k):
                super().__init__(batch=outer.batch)

            def check(self):
                return "stub"

            def send_postings(self, jobs, buttons=True, key_of=None):
                outer.sent.extend(jobs)
                return {(key_of(j) if key_of else j.url): 500 + i
                        for i, j in enumerate(jobs)}

        self.batch = []
        bot.TelegramNotifier = Stub
        os.environ["TELEGRAM_BOT_TOKEN"] = "t"
        os.environ["TELEGRAM_CHAT_ID"] = "1"

    def tearDown(self):
        import os, shutil
        self.bot.SOURCES.clear()
        self.bot.SOURCES.update(self._sources)
        self.bot.TelegramNotifier = self._notifier
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, *extra):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            return self.bot.main(["--source", "greenhouse", "-o", self.out,
                                  "--state", self.out + "_seen.json",
                                  "--days", "0", "-q"] + list(extra))

    def _state(self):
        import json
        with open(self.out + "_seen.json") as fh:
            return json.load(fh)

    def test_message_ids_are_stored_for_what_was_sent(self):
        # Nothing else works without them: an id exists nowhere but the
        # send response, and a tap has no message to edit without one.
        self._run()
        st = self._state()
        self.assertEqual(st["https://x.example/acme"]["message_id"], 500)
        self.assertEqual(st["https://x.example/acme"]["state"], "sent")

    def test_a_muted_company_is_gone_from_the_next_run(self):
        self._run()
        self.sent.clear()
        self.batch = [tap("https://x.example/acme", "muted", update_id=1)]
        self._run("--include-seen" if False else "--max-messages", "0")
        self.assertNotIn("Acme", [j.company for j in self.sent])

    def test_muting_does_not_hide_the_other_companies(self):
        self._run()
        self.batch = [tap("https://x.example/acme", "muted", update_id=1)]
        self.sent.clear()
        self._run("--max-messages", "0")
        st = self._state()
        self.assertEqual(taps.muted_companies(st), {"acme"})
        self.assertIn("Globex", [e.get("company") for e in st.values()
                                 if isinstance(e, dict)])

    def test_no_buttons_skips_the_drain_entirely(self):
        self.batch = [tap("https://x.example/acme", "muted", update_id=1)]
        self._run("--no-buttons")
        self.assertEqual(taps.muted_companies(self._state()), set())


if __name__ == "__main__":
    unittest.main(verbosity=1)
