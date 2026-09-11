#!/usr/bin/env python3
"""Tests for the multi-user bot: subscribers, onboarding, and the fan-out.

Offline like every other suite here — the notifier is a stub that records
calls, so nothing reaches api.telegram.org.

    python3 test_serve.py           # all of it
    python3 test_serve.py -v        # naming each case

Stdlib only, like the crawler itself.
"""

import json
import os
import shutil
import tempfile
import unittest

import jobcrawler as c
from jobcrawler.filters.countries import CA, US
from jobcrawler.notify import fanout, router
from jobcrawler.notify.subscribers import (SEEN_TTL_DAYS, Subscriber,
                                           Subscribers, seen_key)
from jobcrawler.notify.telegram import Blocked


class StubBot:
    """Records what it would send, and can be told to refuse a chat."""

    def __init__(self, blocked=()):
        self.sent = []
        self.postings = []
        self.edits = []
        self.toasts = []
        self.last_message_id = {}
        self.blocked = set(str(b) for b in blocked)

    def send_to(self, chat_id, text, preview=False, markup=None):
        if str(chat_id) in self.blocked:
            raise Blocked("bot was blocked by the user")
        self.sent.append((str(chat_id), text, markup))
        self.last_message_id[str(chat_id)] = len(self.sent)
        return {"message_id": len(self.sent)}

    def send_postings(self, jobs, buttons=True, key_of=None, country=None,
                      chat_id=None):
        if str(chat_id) in self.blocked:
            raise Blocked("bot was blocked by the user")
        self.postings.extend((str(chat_id), j) for j in jobs)
        return {(key_of(j) if key_of else j.url): i for i, j in enumerate(jobs)}

    def edit_markup(self, chat_id, message_id, markup):
        self.edits.append((str(chat_id), message_id, markup))
        return True

    def answer_raw(self, callback_id, text):
        self.toasts.append(text)

    def texts_to(self, chat_id):
        return [t for cid, t, _ in self.sent if cid == str(chat_id)]


def job(title="Backend Engineer", company="Acme", url=None, **over):
    return c.row("greenhouse", title, company, over.pop("location", "Remote - US"),
                 url or f"https://x.example/{abs(hash(title)) % 9999}",
                 over.pop("posted", "2026-09-12"), **over)


# ==========================================================================
# The subscriber store
# ==========================================================================
class TestSubscribers(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "subs.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_subscriber_survives_a_round_trip(self):
        s = Subscribers(self.path)
        s.add("123", roles=["backend", "devops"], country="ca")
        s.save()
        back = Subscribers(self.path).load().get("123")
        self.assertEqual(back.roles, ["backend", "devops"])
        self.assertEqual(back.country, "ca")

    def test_adding_twice_does_not_reset_what_they_chose(self):
        # /start is a button people press twice.
        s = Subscribers(self.path)
        s.add("123", roles=["backend"])
        self.assertEqual(s.add("123", roles=["data"]).roles, ["backend"])

    def test_a_missing_file_is_an_empty_list_not_an_error(self):
        self.assertEqual(len(Subscribers(self.path).load()), 0)

    def test_a_corrupt_file_does_not_take_the_run_down(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(len(Subscribers(self.path).load()), 0)

    def test_seen_is_stored_as_a_hash_not_a_url(self):
        # A subscriber of a year would carry thousands of links otherwise.
        sub = Subscriber("1")
        sub.mark_seen("https://boards.example/very/long/url/123", "2026-09-12")
        self.assertNotIn("boards.example", json.dumps(sub.as_record()))
        self.assertTrue(sub.has_seen("https://boards.example/very/long/url/123"))

    def test_pruning_drops_keys_older_than_any_window(self):
        sub = Subscriber("1")
        sub.mark_seen("https://x/old", "2026-01-01")
        sub.mark_seen("https://x/new", "2026-09-12")
        self.assertEqual(sub.prune("2026-09-12"), 1)
        self.assertFalse(sub.has_seen("https://x/old"))
        self.assertTrue(sub.has_seen("https://x/new"))

    def test_pruning_keeps_anything_a_run_could_still_ask_for(self):
        # The TTL has to exceed the widest --days, or a pruned posting
        # comes back as new.
        self.assertGreater(SEEN_TTL_DAYS, 60)

    def test_paused_subscribers_are_not_active(self):
        s = Subscribers(self.path)
        s.add("1")
        s.add("2").paused = True
        self.assertEqual([x.chat_id for x in s.active()], ["1"])

    def test_muting_is_case_insensitive_and_idempotent(self):
        sub = Subscriber("1")
        self.assertTrue(sub.mute("Acme"))
        self.assertFalse(sub.mute("  acme  "))
        self.assertFalse(sub.wants("ACME"))
        self.assertEqual(len(sub.muted), 1)


# ==========================================================================
# Onboarding
# ==========================================================================
class TestOnboarding(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.subs = Subscribers(os.path.join(self.dir, "s.json"))
        self.bot = StubBot()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_start_subscribes_a_stranger_and_asks_what_they_build(self):
        router.handle_command(self.bot, self.subs, "999", "/start")
        self.assertIsNotNone(self.subs.get("999"))
        self.assertIn("What do you build", self.bot.texts_to("999")[0])

    def test_a_command_from_a_stranger_is_treated_as_start(self):
        # Someone typing /settings at a bot they never started is asking
        # to start it, not to be ignored.
        router.handle_command(self.bot, self.subs, "999", "/settings")
        self.assertIsNotNone(self.subs.get("999"))

    def test_role_buttons_toggle(self):
        self.subs.add("1", roles=[])
        router.handle_setup(self.bot, self.subs, "1", "role:backend")
        self.assertEqual(self.subs.get("1").roles, ["backend"])
        router.handle_setup(self.bot, self.subs, "1", "role:backend")
        self.assertEqual(self.subs.get("1").roles, [])

    def test_done_with_no_role_chosen_says_so_rather_than_proceeding(self):
        self.subs.add("1", roles=[])
        router.handle_setup(self.bot, self.subs, "1", "role:done", "cb")
        self.assertIn("Pick at least one", self.bot.toasts[0])
        self.assertEqual(self.bot.sent, [])

    def test_done_moves_on_to_the_country_question(self):
        self.subs.add("1", roles=["backend"])
        router.handle_setup(self.bot, self.subs, "1", "role:done", "cb")
        self.assertIn("Where can you work", self.bot.texts_to("1")[0])

    def test_choosing_a_country_confirms_the_whole_setup(self):
        self.subs.add("1", roles=["backend"])
        router.handle_setup(self.bot, self.subs, "1", "country:ca")
        self.assertEqual(self.subs.get("1").country, "ca")
        self.assertIn("Set.", self.bot.texts_to("1")[0])

    def test_stop_deletes_everything_about_them(self):
        self.subs.add("1")
        router.handle_command(self.bot, self.subs, "1", "/stop")
        self.assertIsNone(self.subs.get("1"))

    def test_pause_keeps_the_subscriber_but_stops_the_jobs(self):
        self.subs.add("1")
        router.handle_command(self.bot, self.subs, "1", "/pause")
        self.assertTrue(self.subs.get("1").paused)
        router.handle_command(self.bot, self.subs, "1", "/resume")
        self.assertFalse(self.subs.get("1").paused)

    def test_a_command_addressed_to_the_bot_in_a_group_still_works(self):
        router.handle_command(self.bot, self.subs, "1", "/start@getyournewjob_bot")
        self.assertIsNotNone(self.subs.get("1"))

    def test_ordinary_text_is_not_a_command(self):
        self.assertFalse(router.handle_command(self.bot, self.subs, "1", "hello"))


# ==========================================================================
# Whose update is it?
# ==========================================================================
class TestRouting(unittest.TestCase):

    def test_a_message_names_its_chat(self):
        self.assertEqual(
            router.chat_of({"message": {"chat": {"id": 42}}}), "42")

    def test_a_callback_names_the_chat_its_message_is_in(self):
        self.assertEqual(router.chat_of(
            {"callback_query": {"message": {"chat": {"id": 42}}}}), "42")

    def test_a_callback_on_a_vanished_message_falls_back_to_its_sender(self):
        self.assertEqual(router.chat_of(
            {"callback_query": {"from": {"id": 42}}}), "42")

    def test_a_block_is_recognised_as_one(self):
        self.assertTrue(router.is_block(
            {"my_chat_member": {"new_chat_member": {"status": "kicked"}}}))
        self.assertFalse(router.is_block(
            {"my_chat_member": {"new_chat_member": {"status": "member"}}}))


# ==========================================================================
# The fan-out
# ==========================================================================
class TestFanout(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.subs = Subscribers(os.path.join(self.dir, "s.json"))
        self.bot = StubBot()
        self.jobs = [job("Backend Engineer"), job("Android Developer"),
                     job("DevOps Engineer"), job("Data Scientist")]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_each_subscriber_gets_only_their_disciplines(self):
        sub = self.subs.add("1", roles=["backend"])
        got = fanout.for_subscriber(sub, self.jobs, "2026-09-12")
        self.assertEqual([j.title for j in got], ["Backend Engineer"])

    def test_two_roles_get_both(self):
        sub = self.subs.add("1", roles=["backend", "devops"])
        titles = {j.title for j in fanout.for_subscriber(sub, self.jobs, "x")}
        self.assertEqual(titles, {"Backend Engineer", "DevOps Engineer"})

    def test_one_persons_seen_list_does_not_hide_a_job_from_another(self):
        # The reason seen is per subscriber and not shared.
        a = self.subs.add("1", roles=["backend"])
        b = self.subs.add("2", roles=["backend"])
        a.mark_seen(self.jobs[0].url, "2026-09-12")
        self.assertEqual(fanout.for_subscriber(a, self.jobs, "x"), [])
        self.assertEqual(len(fanout.for_subscriber(b, self.jobs, "x")), 1)

    def test_a_muted_company_is_dropped_for_that_subscriber_only(self):
        a = self.subs.add("1", roles=["backend"])
        b = self.subs.add("2", roles=["backend"])
        a.mute("Acme")
        self.assertEqual(fanout.for_subscriber(a, self.jobs, "x"), [])
        self.assertEqual(len(fanout.for_subscriber(b, self.jobs, "x")), 1)

    def test_delivering_marks_them_seen_so_they_are_not_resent(self):
        sub = self.subs.add("1", roles=["backend"])
        batch = fanout.for_subscriber(sub, self.jobs, "2026-09-12")
        fanout.deliver(self.bot, self.subs, sub, batch, US, "2026-09-12",
                       15, c.NullReporter())
        self.assertEqual(fanout.for_subscriber(sub, self.jobs, "x"), [])

    def test_a_block_unsubscribes_rather_than_retrying_forever(self):
        # Telegram returns 403 for a blocked chat forever; keeping the row
        # costs one request per run per departed reader.
        sub = self.subs.add("1", roles=["backend"])
        self.bot.blocked.add("1")
        batch = fanout.for_subscriber(sub, self.jobs, "2026-09-12")
        n = fanout.deliver(self.bot, self.subs, sub, batch, US, "2026-09-12",
                           15, c.NullReporter())
        self.assertEqual(n, 0)
        self.assertIsNone(self.subs.get("1"))

    def test_a_blocked_send_does_not_mark_anything_seen(self):
        sub = Subscriber("1", roles=["backend"])
        self.subs.people["1"] = sub
        self.bot.blocked.add("1")
        batch = fanout.for_subscriber(sub, self.jobs, "2026-09-12")
        fanout.deliver(self.bot, self.subs, sub, batch, US, "2026-09-12",
                       15, c.NullReporter())
        self.assertEqual(sub.seen, {})

    def test_the_overflow_becomes_one_summary(self):
        sub = self.subs.add("1", roles=["all"])
        many = [job(f"Backend Engineer {i}") for i in range(20)]
        fanout.deliver(self.bot, self.subs, sub, many, US, "2026-09-12",
                       5, c.NullReporter())
        self.assertEqual(len(self.bot.postings), 5)
        self.assertEqual(len(self.bot.sent), 1)
        self.assertIn("+15 more", self.bot.sent[0][1])

    def test_everything_sent_is_marked_seen_including_the_summary(self):
        # A digest entry not marked seen comes back as new tomorrow, and
        # every tomorrow after.
        sub = self.subs.add("1", roles=["all"])
        many = [job(f"Backend Engineer {i}") for i in range(20)]
        fanout.deliver(self.bot, self.subs, sub, many, US, "2026-09-12",
                       5, c.NullReporter())
        self.assertEqual(len(sub.seen), 20)


if __name__ == "__main__":
    unittest.main(verbosity=1)
