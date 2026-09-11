#!/usr/bin/env python3
"""Tests for the Telegram bot: formatting, secrets, and the send protocol.

Offline like every other suite here — the notifier's transport is replaced,
so nothing in this file reaches api.telegram.org or needs a token.

    python3 test_notify.py           # all of it
    python3 test_notify.py -v        # naming each case

Stdlib only, like the crawler itself.
"""

import os
import tempfile
import unittest

import jobcrawler as c
from jobcrawler.notify.secrets import load_env_file, redact
from jobcrawler.notify.telegram import (TelegramError, TelegramNotifier,
                                        format_posting)


def job(source="greenhouse", title="Senior Android Engineer", company="Acme",
        **over):
    return c.row(source, title, company, over.pop("location", "Remote - US"),
                 over.pop("url", "https://boards.example/1"),
                 over.pop("posted", "2026-08-01"), **over)


class Fake(TelegramNotifier):
    """A notifier whose transport records calls instead of making them."""

    def __init__(self, fail_on=(), **over):
        super().__init__("token", "123", gap=0.0, **over)
        self.calls = []
        self.fail_on = set(fail_on)

    def _call(self, method, payload):
        self.calls.append((method, payload))
        text = payload.get("text", "")
        if any(f in text for f in self.fail_on):
            raise TelegramError("rejected by the test")
        return {"message_id": len(self.calls), "username": "testbot"}

    @property
    def sent(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


# ==========================================================================
# Formatting one posting
# ==========================================================================
class TestFormat(unittest.TestCase):

    def test_the_title_links_to_the_posting(self):
        out = format_posting(job(url="https://boards.example/42"))
        self.assertIn('<a href="https://boards.example/42">', out)
        self.assertIn("Senior Android Engineer", out)

    def test_a_posting_with_no_url_still_renders(self):
        out = format_posting(job(url=""))
        self.assertIn("Senior Android Engineer", out)
        self.assertNotIn("<a href", out)

    def test_html_in_a_title_is_escaped_not_rendered(self):
        # A title is untrusted text from a job board; unescaped it would
        # either break parse_mode=HTML or inject markup into the message.
        out = format_posting(job(title="Android <b>Dev</b> & Co"))
        self.assertIn("&lt;b&gt;", out)
        self.assertIn("&amp;", out)

    def test_the_source_is_named_so_a_reader_can_judge_the_link(self):
        self.assertIn("via greenhouse", format_posting(job()))

    def test_a_stated_salary_is_shown_as_a_range(self):
        out = format_posting(job(salary_min=150000, salary_max=190000,
                                 salary_currency="usd"))
        self.assertIn("150,000–190,000 USD", out)

    def test_a_predicted_salary_is_never_shown_as_fact(self):
        # Adzuna's estimate is a model's guess, not the employer's number.
        out = format_posting(job(salary_min=150000, salary_max=190000,
                                 salary_predicted="yes"))
        self.assertNotIn("150,000", out)

    def test_absent_fields_are_omitted_rather_than_left_empty(self):
        out = format_posting(job(location="", posted="", remote=False))
        self.assertNotIn("📍", out)
        self.assertNotIn("🗓", out)

    def test_a_message_never_exceeds_the_api_limit(self):
        out = format_posting(job(title="A" * 6000))
        self.assertLessEqual(len(out), 4096)


# ==========================================================================
# Sending
# ==========================================================================
class TestSend(unittest.TestCase):

    def test_each_posting_becomes_its_own_message(self):
        bot = Fake()
        sent = bot.send_postings([job(title="Android Engineer",
                                      url="https://x.example/1"),
                                  job(title="Mobile Engineer",
                                      url="https://x.example/2")])
        # A mapping, not a count: the message_id it carries is what a later
        # tap edits, and it exists nowhere but the send response.
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(bot.sent), 2)
        self.assertTrue(all(isinstance(v, int) for v in sent.values()))

    def test_one_failed_posting_does_not_silence_the_others(self):
        # A single unsendable title must cost one job, not the whole morning.
        bot = Fake(fail_on=["Poison"], report=c.NullReporter())
        sent = bot.send_postings([job(title="Android Engineer",
                                      url="https://x.example/1"),
                                  job(title="Poison Android Engineer",
                                      url="https://x.example/2"),
                                  job(title="Mobile Engineer",
                                      url="https://x.example/3")])
        self.assertEqual(len(sent), 2)

    def test_sending_nothing_is_not_an_error(self):
        self.assertEqual(Fake().send_postings([]), {})

    def test_link_previews_are_off_so_the_feed_stays_scannable(self):
        bot = Fake()
        bot.send("hi")
        self.assertTrue(bot.calls[0][1]["disable_web_page_preview"])

    def test_missing_credentials_are_refused_at_construction(self):
        with self.assertRaises(TelegramError):
            TelegramNotifier("", "123")
        with self.assertRaises(TelegramError):
            TelegramNotifier("token", "")


# ==========================================================================
# What the schedule crawls, and how hard it hits it
# ==========================================================================
class TestBotSources(unittest.TestCase):

    def test_every_scheduled_source_is_a_real_one(self):
        from jobcrawler.notify.bot import BOT_SOURCES
        unknown = [s for s in BOT_SOURCES if s not in c.SOURCES]
        self.assertEqual(unknown, [], f"not in the registry: {unknown}")

    def test_serpapi_is_never_on_the_schedule(self):
        # 250 searches a MONTH free; one run of 8 keywords spends 8. Twice
        # daily is ~480 a month, so this would break the tier in a fortnight.
        from jobcrawler.notify.bot import BOT_SOURCES
        self.assertNotIn("serpapi", BOT_SOURCES)

    def test_no_blocked_source_is_on_the_schedule(self):
        from jobcrawler.notify.bot import BOT_SOURCES
        from jobcrawler.sources.blocked import BLOCKED
        self.assertEqual([s for s in BOT_SOURCES if s in BLOCKED], [])

    def test_linkedin_keeps_its_pacing(self):
        # The bot built RateLimiter(default=HostPolicy(gap=0.0)), which reads
        # as "no pacing" and would become exactly that if policies= were ever
        # passed. LinkedIn throttles by IP, so its several-second gap has to
        # survive however the limiter is constructed.
        from jobcrawler.notify.bot import build, parser
        args = parser().parse_args([])
        _, ctx = build(args, c.NullReporter(), 14, "2026-09-07", {})
        policy = ctx.fetch.limiter.policy_for(
            "https://www.linkedin.com/jobs-guest/jobs/api/search")
        self.assertGreaterEqual(policy.gap, 3.0)

    def test_the_delay_flag_reaches_linkedins_policy(self):
        from jobcrawler.notify.bot import build, parser
        args = parser().parse_args(["--delay", "9"])
        _, ctx = build(args, c.NullReporter(), 14, "2026-09-07", {})
        self.assertEqual(ctx.fetch.limiter.policy_for(
            "https://www.linkedin.com/x").gap, 9.0)

    def test_the_ats_boards_are_still_unpaced(self):
        # The ATS crawls run a six-worker pool against APIs built to be
        # polled; pacing them would turn a 46-board sweep into minutes.
        from jobcrawler.notify.bot import build, parser
        args = parser().parse_args([])
        _, ctx = build(args, c.NullReporter(), 14, "2026-09-07", {})
        self.assertEqual(ctx.fetch.limiter.policy_for(
            "https://boards-api.greenhouse.io/v1/boards/x/jobs").gap, 0.0)


# ==========================================================================
# The scrape sources, whose last line only runs after a successful crawl
# ==========================================================================
class TestScrapeSources(unittest.TestCase):

    def test_builtin_summarises_without_subscripting_a_posting(self):
        # builtin.py ended with `j['remote']`, left behind when the record
        # became a Posting. It is the last line of the function, so it only
        # ever runs after a crawl succeeds — no offline test reached it, and
        # the source died with a TypeError the moment Built In answered.
        from jobcrawler.sources.boards.builtin import crawl_builtin

        page = ('<div data-id="job-card" data-alias="acme">'
                '<a data-id="job-card-title">Senior Android Engineer</a>'
                '<div data-id="company-title"><span>Acme</span></div>'
                '</div>')

        class OnePage:
            def get(self, url, **kw):
                return page

            def get_json(self, url, **kw):
                return {}

        ctx = c.RunContext(fetch=OnePage(), report=c.NullReporter())
        cfg = c.CrawlConfig(keywords=("Android Developer",))
        crawl_builtin(cfg, ctx)   # the assertion is that this does not raise


# ==========================================================================
# The flood guard, and the backfill it has to tell apart from a lost file
# ==========================================================================
class TestFloodGuard(unittest.TestCase):
    """--max-messages against --newest, exercised through main().

    Driven end to end with a stub source rather than by calling the guard
    directly: what matters is not which branch runs but what ends up in the
    state file afterwards, and only main() writes that.
    """

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "run")
        self.sent = []
        self.digests = []

        import jobcrawler.notify.bot as bot
        self.bot = bot
        self._saved_sources = dict(bot.SOURCES)
        self._saved_notifier = bot.TelegramNotifier

        # 40 postings that must survive select() intact: distinct companies
        # so dedupe_key() cannot collapse them, and distinct dates so the
        # newest-N slice has a defined answer. Dates descend from 2026-08-31
        # (jobs 1..28) into July, keeping every one inside a 60-day window.
        made = []
        for i in range(40):
            day = 31 - i
            date = (f"2026-08-{day:02d}" if day >= 1
                    else f"2026-07-{31 + day:02d}")
            made.append(c.row("greenhouse", f"Android Engineer {i}",
                              f"Company{i}", "Remote - US",
                              f"https://x.example/{i}", date))
        bot.SOURCES.clear()
        bot.SOURCES["greenhouse"] = lambda cfg, ctx: list(made)

        outer = self

        class Stub:
            def __init__(self, *a, **k):
                pass

            def check(self):
                return "stubbot"

            def send_postings(self, jobs, buttons=True, key_of=None, country=None):
                outer.sent.extend(jobs)
                return {(key_of(j) if key_of else j.url): i
                        for i, j in enumerate(jobs)}

            def send(self, text, preview=False, markup=None):
                outer.digests.append(text)
                return {"message_id": len(outer.digests)}

            # No taps in these tests, but the methods have to exist: a
            # missing one is swallowed by drain()'s except and the warning
            # it prints is the only sign the path was never exercised.
            def updates(self, offset=None, limit=100):
                return []

            def acknowledge(self, update_id):
                pass

        bot.TelegramNotifier = Stub
        # Restored in tearDown: leaking these into the real environment is
        # what made an unrelated secrets test read 't' as its token.
        self._saved_env = dict(os.environ)
        os.environ["TELEGRAM_BOT_TOKEN"] = "t"
        os.environ["TELEGRAM_CHAT_ID"] = "1"

    def tearDown(self):
        import shutil
        self.bot.SOURCES.clear()
        self.bot.SOURCES.update(self._saved_sources)
        self.bot.TelegramNotifier = self._saved_notifier
        os.environ.clear()
        os.environ.update(self._saved_env)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, *extra):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            return self._main(*extra)

    def _main(self, *extra):
        return self.bot.main(["--source", "greenhouse", "-o", self.out,
                              "--days", "0",
                              "--state", self.out + "_seen.json", "-q"]
                             + list(extra))

    def _seen(self):
        import json
        with open(self.out + "_seen.json") as fh:
            return [k for k in json.load(fh) if not k.startswith("_")]

    def test_the_overflow_is_summarised_not_refused(self):
        # The cap was written when forty new postings meant a lost state
        # file. With fourteen sources and seventeen role profiles forty is
        # an ordinary day, so refusing became a failure on healthy runs.
        self.assertEqual(self._run("--max-messages", "10"), 0)
        self.assertEqual(len(self.sent), 10)
        self.assertTrue(self.digests, "the overflow was never summarised")

    def test_the_summary_names_what_it_holds(self):
        self._run("--max-messages", "10")
        self.assertIn("+30 more", self.digests[0])

    def test_summarised_jobs_are_marked_seen_and_never_resent(self):
        # The failure this guards against: a digest entry not written to
        # state comes back as new tomorrow, and every tomorrow after.
        self._run("--max-messages", "10")
        self.assertEqual(len(self._seen()), 40)
        self.sent.clear()
        self.digests.clear()
        self.assertEqual(self._run("--max-messages", "10"), 0)
        self.assertEqual(self.sent, [])

    def test_no_digest_restores_the_refusal(self):
        # A genuinely lost state file is still worth stopping for, and only
        # the reader knows which of the two they are looking at.
        self.assertEqual(self._run("--max-messages", "10", "--no-digest"), 3)
        self.assertEqual(self.sent, [])
        self.assertFalse(os.path.exists(self.out + "_seen.json"))

    def test_newest_sends_exactly_the_limit(self):
        self.assertEqual(self._run("--max-messages", "10", "--newest"), 0)
        self.assertEqual(len(self.sent), 10)

    def test_a_run_under_the_cap_sends_no_summary(self):
        self._run("--max-messages", "100")
        self.assertEqual(self.digests, [])

    def test_newest_sends_the_newest_ones(self):
        self._run("--max-messages", "5", "--newest")
        dates = [j.posted for j in self.sent]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_the_held_back_ones_are_marked_seen_not_resent(self):
        # The whole point: they must not come back as "new" tomorrow.
        self._run("--max-messages", "10", "--newest")
        self.assertEqual(len(self._seen()), 40)
        self.sent.clear()
        self.assertEqual(self._run("--max-messages", "10", "--newest"), 0)
        self.assertEqual(self.sent, [])

    def test_under_the_limit_newest_changes_nothing(self):
        self.assertEqual(self._run("--max-messages", "100", "--newest"), 0)
        self.assertEqual(len(self.sent), 40)

    def test_zero_means_no_limit(self):
        self.assertEqual(self._run("--max-messages", "0"), 0)
        self.assertEqual(len(self.sent), 40)


# ==========================================================================
# Credentials
# ==========================================================================
class TestSecrets(unittest.TestCase):

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def _write(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        fh.write(text)
        fh.close()
        return fh.name

    def test_values_are_read_from_the_file(self):
        load_env_file(self._write("TELEGRAM_BOT_TOKEN=abc123\n"))
        self.assertEqual(os.environ["TELEGRAM_BOT_TOKEN"], "abc123")

    def test_the_real_environment_always_wins(self):
        # On CI the secrets arrive as environment variables; a stray .env in
        # a checkout must not be able to shadow them.
        os.environ["TELEGRAM_BOT_TOKEN"] = "from-ci"
        load_env_file(self._write("TELEGRAM_BOT_TOKEN=from-file\n"))
        self.assertEqual(os.environ["TELEGRAM_BOT_TOKEN"], "from-ci")

    def test_comments_blanks_quotes_and_export_are_all_tolerated(self):
        load_env_file(self._write(
            "# a comment\n\nexport A=1\nB='two'\nC=\"three\"\n"))
        self.assertEqual((os.environ["A"], os.environ["B"], os.environ["C"]),
                         ("1", "two", "three"))

    def test_a_hash_inside_a_value_is_kept(self):
        # Tokens contain '#'; stripping it as a comment corrupts the key.
        load_env_file(self._write("K=abc#def\n"))
        self.assertEqual(os.environ["K"], "abc#def")

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(load_env_file("/nonexistent/.env"), {})

    def test_redact_never_reveals_a_usable_token(self):
        # Actions logs are public on a public repo.
        out = redact("8123456789:AAHsecretsecret")
        self.assertNotIn("secret", out)
        self.assertEqual(redact(""), "(unset)")


if __name__ == "__main__":
    unittest.main(verbosity=1)
