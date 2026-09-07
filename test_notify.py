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
        sent = bot.send_postings([job(title="Android Engineer"),
                                  job(title="Mobile Engineer")])
        self.assertEqual(sent, 2)
        self.assertEqual(len(bot.sent), 2)

    def test_one_failed_posting_does_not_silence_the_others(self):
        # A single unsendable title must cost one job, not the whole morning.
        bot = Fake(fail_on=["Poison"], report=c.NullReporter())
        sent = bot.send_postings([job(title="Android Engineer"),
                                  job(title="Poison Android Engineer"),
                                  job(title="Mobile Engineer")])
        self.assertEqual(sent, 2)

    def test_sending_nothing_is_not_an_error(self):
        self.assertEqual(Fake().send_postings([]), 0)

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
