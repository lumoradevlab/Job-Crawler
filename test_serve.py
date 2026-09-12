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
from jobcrawler.notify import (actions, fanout, onboarding, router,
                                serve, usertaps)
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
        self.confirmed = []
        self.restored = []
        self.marked = []
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

    def answer(self, callback_id, action):
        self.toasts.append(action)

    def ask_confirm_in(self, chat_id, message_id, url=None):
        self.confirmed.append((str(chat_id), message_id))
        return True

    def restore_buttons_in(self, chat_id, message_id, url=None):
        self.restored.append((str(chat_id), message_id))
        return True

    def mark_in(self, chat_id, message_id, text, action):
        self.marked.append((str(chat_id), message_id, action))
        return True

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
        s.add("123", roles=["backend", "devops"], countries=["us", "ca"])
        s.save()
        back = Subscribers(self.path).load().get("123")
        self.assertEqual(back.roles, ["backend", "devops"])
        self.assertEqual(back.countries, ["us", "ca"])

    def test_a_row_written_before_multi_country_still_loads(self):
        # The old shape: one "country" string. Nobody should be asked again
        # for an answer they already gave.
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"123": {"roles": ["backend"], "country": "ca"}}, fh)
        self.assertEqual(Subscribers(self.path).load().get("123").countries,
                         ["ca"])

    def test_the_old_shape_is_rewritten_on_the_next_save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"123": {"roles": ["backend"], "country": "ca"}}, fh)
        s = Subscribers(self.path).load()
        s.save()
        with open(self.path, encoding="utf-8") as fh:
            record = json.load(fh)["123"]
        self.assertEqual(record["countries"], ["ca"])
        self.assertNotIn("country", record)

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

    def test_the_country_picker_offers_a_way_to_finish(self):
        # Multi-select has no other way out: without Done a reader taps the
        # countries they want and is then stranded mid-signup.
        rows = onboarding.country_keyboard(["us"])["inline_keyboard"]
        taps = [b["callback_data"] for row in rows for b in row]
        self.assertIn("country:done", taps)

    def test_done_confirms_the_whole_setup(self):
        self.subs.add("1", roles=["backend"], countries=["ca"])
        router.handle_setup(self.bot, self.subs, "1", "country:done", "cb")
        self.assertIn("Set.", self.bot.texts_to("1")[0])
        self.assertIn("Canada", self.bot.texts_to("1")[0])

    def test_both_countries_can_be_held_at_once(self):
        # The whole point: a Canadian reader wants the US postings too.
        self.subs.add("1", roles=["backend"], countries=[])
        router.handle_setup(self.bot, self.subs, "1", "country:us")
        router.handle_setup(self.bot, self.subs, "1", "country:ca")
        self.assertEqual(set(self.subs.get("1").countries), {"us", "ca"})

    def test_the_confirmation_names_both(self):
        self.subs.add("1", roles=["backend"], countries=["us", "ca"])
        router.handle_setup(self.bot, self.subs, "1", "country:done", "cb")
        said = self.bot.texts_to("1")[0]
        self.assertIn("United States and Canada", said)

    def test_tapping_a_chosen_country_again_removes_it(self):
        self.subs.add("1", roles=["backend"], countries=["us", "ca"])
        router.handle_setup(self.bot, self.subs, "1", "country:us")
        self.assertEqual(self.subs.get("1").countries, ["ca"])

    def test_done_with_no_country_says_so_rather_than_proceeding(self):
        self.subs.add("1", roles=["backend"], countries=[])
        router.handle_setup(self.bot, self.subs, "1", "country:done", "cb")
        self.assertIn("Pick at least one", self.bot.toasts[0])
        self.assertEqual(self.bot.texts_to("1"), [])

    def test_choosing_a_country_ticks_it(self):
        # The choice saved and the tick did not appear, so tapping again
        # looked like nothing happening — twice.
        self.subs.add("1", roles=["backend"], countries=[])
        self.bot.last_message_id["1"] = 42
        router.handle_setup(self.bot, self.subs, "1", "country:ca")
        self.assertTrue(self.bot.edits, "the keyboard was never redrawn")
        labels = [b["text"] for row in self.bot.edits[-1][2]["inline_keyboard"]
                  for b in row]
        self.assertTrue(any(l.startswith("✓") and "Canada" in l for l in labels),
                        labels)

    def test_adding_the_second_country_leaves_the_first_ticked(self):
        self.subs.add("1", roles=["backend"], countries=["ca"])
        self.bot.last_message_id["1"] = 42
        router.handle_setup(self.bot, self.subs, "1", "country:us")
        labels = [b["text"] for row in self.bot.edits[-1][2]["inline_keyboard"]
                  for b in row]
        self.assertTrue(any(l.startswith("✓") and "United States" in l
                            for l in labels), labels)
        self.assertTrue(any(l.startswith("✓") and "Canada" in l
                            for l in labels), labels)

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


# ==========================================================================
# A tap belongs to the person who made it
# ==========================================================================
class TestCountrySelection(unittest.TestCase):
    """Which crawls a run does, and who each one is sent to."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.subs = Subscribers(os.path.join(self.dir, "s.json"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_one_country_each_is_crawled_once(self):
        self.subs.add("1", countries=["us"])
        self.subs.add("2", countries=["ca"])
        self.assertEqual(
            serve.countries_to_crawl(self.subs.active(), ["us", "ca"]),
            ["ca", "us"])

    def test_a_country_nobody_asked_for_is_not_crawled(self):
        # The crawl is the expensive half — four minutes a country.
        self.subs.add("1", countries=["us"])
        self.assertEqual(
            serve.countries_to_crawl(self.subs.active(), ["us", "ca"]), ["us"])

    def test_both_countries_from_one_subscriber_crawl_both(self):
        self.subs.add("1", countries=["us", "ca"])
        self.assertEqual(
            serve.countries_to_crawl(self.subs.active(), ["us", "ca"]),
            ["ca", "us"])

    def test_the_flag_still_limits_what_is_crawled(self):
        self.subs.add("1", countries=["us", "ca"])
        self.assertEqual(
            serve.countries_to_crawl(self.subs.active(), ["us"]), ["us"])

    def test_a_paused_subscriber_does_not_cause_a_crawl(self):
        self.subs.add("1", countries=["ca"], paused=True)
        self.assertEqual(serve.countries_to_crawl(self.subs.active(),
                                                  ["us", "ca"]), [])

    def test_someone_who_chose_both_is_sent_both(self):
        both = self.subs.add("1", countries=["us", "ca"])
        just_us = self.subs.add("2", countries=["us"])
        active = self.subs.active()
        self.assertEqual(serve.subscribers_for(active, "us"), [both, just_us])
        self.assertEqual(serve.subscribers_for(active, "ca"), [both])


class TestSharedBudget(unittest.TestCase):
    """--max-messages is per subscriber per run, not per country."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.subs = Subscribers(os.path.join(self.dir, "s.json"))
        self.bot = StubBot()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_spent_budget_sends_the_rest_as_one_summary(self):
        sub = self.subs.add("1", roles=["backend"])
        jobs = [job("Backend Engineer %d" % i) for i in range(3)]
        sent = fanout.deliver(self.bot, self.subs, sub, jobs, US,
                              "2026-09-12", 0, c.NullReporter())
        self.assertEqual(sent, 0)
        self.assertEqual(len(self.bot.texts_to("1")), 1)

    def test_the_summary_does_not_claim_messages_that_are_not_there(self):
        sub = self.subs.add("1", roles=["backend"])
        jobs = [job("Backend Engineer %d" % i) for i in range(3)]
        fanout.deliver(self.bot, self.subs, sub, jobs, US, "2026-09-12", 0,
                       c.NullReporter())
        self.assertNotIn("newest 0", self.bot.texts_to("1")[0])

    def test_the_budget_spans_the_countries_rather_than_resetting(self):
        # A reader who asked for both must not get twice the flood the cap
        # exists to prevent, so the second country inherits what is left.
        sub = self.subs.add("1", roles=["backend"], countries=["us", "ca"])
        spent = {}
        report = c.NullReporter()
        first = [job("Backend Engineer US %d" % i) for i in range(2)]
        serve.send_round(self.bot, self.subs, [sub], first, US, "2026-09-12",
                         2, spent, report)
        self.assertEqual(spent["1"], 2)

        second = [job("Backend Engineer CA %d" % i) for i in range(2)]
        serve.send_round(self.bot, self.subs, [sub], second, CA, "2026-09-12",
                         2, spent, report)
        individually = [j.title for _, j in self.bot.postings]
        self.assertEqual(len(individually), 2, individually)
        self.assertTrue(all("US" in t for t in individually), individually)

    def test_a_worldwide_posting_arrives_once_not_once_per_country(self):
        # It names no country and so qualifies under both, turning up in
        # each crawl. The seen list is what stops it being sent twice.
        sub = self.subs.add("1", roles=["backend"], countries=["us", "ca"])
        worldwide = job("Backend Engineer", location="Remote - Worldwide")
        spent, report = {}, c.NullReporter()
        serve.send_round(self.bot, self.subs, [sub], [worldwide], US,
                         "2026-09-12", 10, spent, report)
        serve.send_round(self.bot, self.subs, [sub], [worldwide], CA,
                         "2026-09-12", 10, spent, report)
        self.assertEqual(len(self.bot.postings), 1, self.bot.postings)
        self.assertEqual(self.bot.texts_to("1"), [])

    def test_a_reader_not_yet_in_the_ledger_gets_the_whole_limit(self):
        # Counting upwards means an absent entry reads as "nothing spent".
        # A remaining-budget dict would read it as "nothing left" and digest
        # everything they were owed, silently.
        sub = self.subs.add("1", roles=["backend"])
        serve.send_round(self.bot, self.subs, [sub],
                         [job("Backend Engineer")], US, "2026-09-12",
                         5, {}, c.NullReporter())
        self.assertEqual(len(self.bot.postings), 1)
        self.assertEqual(self.bot.texts_to("1"), [])

    def test_the_second_country_still_arrives_as_a_summary(self):
        sub = self.subs.add("1", roles=["backend"], countries=["us", "ca"])
        serve.send_round(self.bot, self.subs, [sub],
                         [job("Backend Engineer CA")], CA, "2026-09-12",
                         2, {"1": 2}, c.NullReporter())
        self.assertEqual(len(self.bot.texts_to("1")), 1)
        self.assertIn("Backend Engineer CA", self.bot.texts_to("1")[0])

    def test_everything_is_still_marked_seen_when_the_budget_is_spent(self):
        # Otherwise the second country's digest arrives again tomorrow.
        sub = self.subs.add("1", roles=["backend"])
        jobs = [job("Backend Engineer %d" % i) for i in range(3)]
        fanout.deliver(self.bot, self.subs, sub, jobs, US, "2026-09-12", 0,
                       c.NullReporter())
        self.assertTrue(all(sub.has_seen(j.url) for j in jobs))


class TestPerUserTaps(unittest.TestCase):
    """The same button means different things to different readers.

    One reader muting Acme must not silence it for anyone else, and one
    reader's /applied must not list another's jobs. That is the whole
    reason a tap resolves to a subscriber before it resolves to a posting.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.subs = Subscribers(os.path.join(self.dir, "s.json"))
        self.bot = StubBot()
        self.job = job("Backend Engineer", "Acme", url="https://x.example/1")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _subscribed(self, chat_id="1"):
        sub = self.subs.add(chat_id, roles=["backend"])
        key = sub.record_sent(self.job, 500, "2026-09-12")
        return sub, key

    def _tap(self, action, key, chat_id="1", message_id=500):
        return {"id": "cb", "data": actions.encode(action, self.job.url),
                "message": {"message_id": message_id,
                            "chat": {"id": int(chat_id)},
                            "text": "Backend Engineer"}}

    def test_a_button_resolves_to_the_posting_it_was_sent_with(self):
        # The digest a button carries and the key a posting is stored under
        # have to be the same function, or a tap is unresolvable.
        sub, key = self._subscribed()
        self.assertEqual(actions.tap_id(self.job.url), key)
        self.assertIn(key, sub.sent)

    def test_applying_records_it_against_that_subscriber(self):
        sub, key = self._subscribed()
        usertaps.handle_tap(self.bot, sub, self._tap("applied", key), "2026-09-13")
        self.assertIn(self.job.url, sub.applied)
        self.assertEqual(sub.applied[self.job.url]["when"], "2026-09-13")
        self.assertEqual(sub.sent[key]["state"], "applied")

    def test_one_readers_tap_leaves_another_alone(self):
        a, key = self._subscribed("1")
        b, _ = self._subscribed("2")
        usertaps.handle_tap(self.bot, a, self._tap("applied", key), "2026-09-13")
        self.assertEqual(b.applied, {})
        self.assertEqual(b.sent[key]["state"], "sent")

    def test_muting_silences_the_company_for_that_reader_only(self):
        a, key = self._subscribed("1")
        b, _ = self._subscribed("2")
        usertaps.handle_tap(self.bot, a, self._tap("muted", key), "2026-09-13")
        self.assertFalse(a.wants("Acme"))
        self.assertTrue(b.wants("Acme"))

    def test_the_message_is_rewritten_in_that_readers_chat(self):
        sub, key = self._subscribed("7")
        usertaps.handle_tap(self.bot, sub, self._tap("applied", key, "7"),
                            "2026-09-13")
        self.assertEqual(self.bot.marked, [("7", 500, "applied")])

    def test_opening_asks_rather_than_recording(self):
        sub, key = self._subscribed()
        usertaps.handle_tap(self.bot, sub, self._tap("opening", key), "2026-09-13")
        self.assertEqual(sub.sent[key]["state"], "sent")
        self.assertEqual(sub.applied, {})
        self.assertEqual(self.bot.confirmed, [("1", 500)])

    def test_declining_restores_the_buttons_and_records_nothing(self):
        sub, key = self._subscribed()
        usertaps.handle_tap(self.bot, sub, self._tap("opening", key), "2026-09-13")
        usertaps.handle_tap(self.bot, sub, self._tap("cancel", key), "2026-09-13")
        self.assertNotIn("asked_at", sub.sent[key])
        self.assertEqual(self.bot.restored, [("1", 500)])

    def test_applying_removes_it_from_saved(self):
        # A job you applied to is no longer one you are meaning to look at.
        sub, key = self._subscribed()
        usertaps.handle_tap(self.bot, sub, self._tap("saved", key), "2026-09-13")
        usertaps.handle_tap(self.bot, sub, self._tap("applied", key), "2026-09-14")
        self.assertIn(self.job.url, sub.applied)
        self.assertNotIn(self.job.url, sub.saved)

    def test_the_same_tap_twice_changes_nothing(self):
        sub, key = self._subscribed()
        for _ in range(3):
            usertaps.handle_tap(self.bot, sub,
                                self._tap("muted", key), "2026-09-13")
        self.assertEqual(len(sub.muted), 1)

    def test_a_tap_on_a_pruned_posting_is_survivable(self):
        sub = self.subs.add("1", roles=["backend"])
        self.assertTrue(usertaps.handle_tap(
            self.bot, sub, self._tap("applied", "deadbeef00"), "2026-09-13"))
        self.assertEqual(sub.applied, {})

    def test_the_reader_is_answered_even_then(self):
        sub = self.subs.add("1", roles=["backend"])
        usertaps.handle_tap(self.bot, sub,
                            self._tap("applied", "deadbeef00"), "2026-09-13")
        self.assertEqual(self.bot.toasts, ["applied"])

    def test_delivering_stores_what_a_tap_will_need(self):
        # Without the message_id there is no message to rewrite; without the
        # company there is nothing to mute.
        sub = self.subs.add("1", roles=["backend"])
        fanout.deliver(self.bot, self.subs, sub, [self.job], US,
                       "2026-09-12", 15, c.NullReporter())
        record = sub.sent[actions.tap_id(self.job.url)]
        self.assertEqual(record["company"], "Acme")
        self.assertIsNotNone(record["message_id"])

    def test_pruning_keeps_what_the_reader_acted_on(self):
        # /applied is a record, not a feed: an application from last year is
        # still an application.
        sub, key = self._subscribed()
        usertaps.handle_tap(self.bot, sub, self._tap("applied", key), "2026-01-01")
        sub.seen[key] = "2026-01-01"
        sub.prune("2026-09-13")
        self.assertIn(key, sub.sent)
        self.assertIn(self.job.url, sub.applied)

    def test_pruning_drops_what_they_ignored(self):
        sub, key = self._subscribed()
        sub.seen[key] = "2026-01-01"
        sub.prune("2026-09-13")
        self.assertNotIn(key, sub.sent)


if __name__ == "__main__":
    unittest.main(verbosity=1)
