#!/usr/bin/env python3
"""Tests for the run's stages: collecting, selecting, and the new/seen split.

These were the middle of main() — reachable only by running the whole CLI,
which meant they were not tested at all. As functions over lists they need
nothing but a config and a couple of postings.

    python3 test_pipeline.py           # all of it
    python3 test_pipeline.py -v        # naming each case

Stdlib only, like the crawler itself.
"""

import unittest

import jobcrawler as c


def cfg(sources, **over):
    filters = c.FilterConfig(**over)
    return c.CrawlConfig(sources=tuple(sources), filters=filters)


def ctx(**over):
    return c.RunContext(report=c.NullReporter(), **over)


def job(source="greenhouse", title="Senior Android Engineer", company="Acme",
        **over):
    return c.row(source, title, company, over.pop("location", "Remote - US"),
                 over.pop("url", f"https://{source}.example/1"),
                 over.pop("posted", "2026-08-01"), **over)


# ==========================================================================
# Collecting — and which sources may advance their clock afterwards
# ==========================================================================
class TestCollect(unittest.TestCase):

    def test_every_source_runs_and_its_postings_are_gathered(self):
        out = c.collect(cfg(["a", "b"]), ctx(),
                        {"a": lambda cfg, ctx: [job(source="a")],
                         "b": lambda cfg, ctx: [job(source="b")]})
        self.assertEqual(sorted(j.source for j in out.postings), ["a", "b"])
        self.assertEqual(out.succeeded, {"a", "b"})

    def test_one_source_failing_does_not_take_the_run_down(self):
        def boom(cfg, ctx):
            raise RuntimeError("board changed its HTML")

        out = c.collect(cfg(["bad", "good"]), ctx(),
                        {"bad": boom, "good": lambda cfg, ctx: [job(source="good")]})
        self.assertEqual([j.source for j in out.postings], ["good"])

    def test_a_source_that_failed_does_not_advance_its_clock(self):
        # The bug this exists for: the failed source used to be recorded as
        # having run, so the next day's window covered only the day since and
        # everything it would have returned was never asked for again.
        def boom(cfg, ctx):
            raise RuntimeError("nope")

        out = c.collect(cfg(["bad", "good"]), ctx(),
                        {"bad": boom, "good": lambda cfg, ctx: []})
        self.assertEqual(out.succeeded, {"good"})
        self.assertNotIn("bad", out.succeeded)

    def test_a_source_returning_nothing_still_counts_as_having_worked(self):
        # A quiet week is a real answer, and must not widen tomorrow's window.
        out = c.collect(cfg(["quiet"]), ctx(), {"quiet": lambda cfg, ctx: []})
        self.assertEqual(out.succeeded, {"quiet"})

    def test_a_total_network_blackout_advances_nothing(self):
        # Every source can return cleanly and still have learned nothing.
        # Advancing on that loses the window exactly as an exception would.
        run = ctx()
        run.fetch.stats.record_failure(
            c.Failure("https://x.example/", "SSLCertVerificationError", "no CA"))
        out = c.collect(cfg(["a"]), run, {"a": lambda cfg, ctx: []})
        self.assertTrue(run.fetch.stats.total_blackout())
        self.assertEqual(out.succeeded, set())


# ==========================================================================
# Selecting — the gate, the duplicate collapse and the order
# ==========================================================================
class TestSelect(unittest.TestCase):

    def test_a_rejected_posting_does_not_survive(self):
        jobs, _ = c.select([job(title="Account Executive")], c.FilterConfig())
        self.assertEqual(jobs, [])

    def test_reasons_are_only_collected_when_asked_for(self):
        # --why is the only reader; building it always would keep every
        # dropped posting alive for the length of the run.
        _, quiet = c.select([job(title="Account Executive")], c.FilterConfig())
        self.assertEqual(quiet, [])
        _, loud = c.select([job(title="Account Executive")], c.FilterConfig(),
                           explain=True)
        self.assertEqual(len(loud), 1)
        self.assertTrue(loud[0][1].startswith("not-mobile"))

    def test_the_better_ranked_source_wins_a_duplicate(self):
        # An ATS link outlives the aggregator redirect that points at it.
        jobs, _ = c.select([job(source="linkedin"), job(source="greenhouse")],
                           c.FilterConfig())
        self.assertEqual([j.source for j in jobs], ["greenhouse"])

    def test_the_winner_is_the_same_whichever_order_they_arrive(self):
        forward, _ = c.select([job(source="greenhouse"), job(source="linkedin")],
                              c.FilterConfig())
        back, _ = c.select([job(source="linkedin"), job(source="greenhouse")],
                           c.FilterConfig())
        self.assertEqual([j.source for j in forward], [j.source for j in back])

    def test_salary_survives_losing_the_duplicate_contest(self):
        # The aggregator is the only source that states pay; dropping its
        # record must not drop the one fact it contributed.
        jobs, _ = c.select([job(source="adzuna", salary_min=180000.0,
                                salary_max=200000.0),
                            job(source="greenhouse")], c.FilterConfig())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "greenhouse")
        self.assertEqual(jobs[0].salary_min, 180000.0)

    def test_salary_is_not_taken_from_a_loser_when_the_winner_has_it(self):
        jobs, _ = c.select([job(source="adzuna", salary_min=100000.0),
                            job(source="greenhouse", salary_min=180000.0)],
                           c.FilterConfig())
        self.assertEqual(jobs[0].salary_min, 180000.0)

    def test_min_salary_keeps_postings_that_state_no_pay(self):
        stated = job(url="https://a/1", salary_max=90000.0)
        silent = job(url="https://a/2", company="Other")
        jobs, _ = c.select([stated, silent],
                           c.FilterConfig(min_salary=150000))
        self.assertEqual([j.url for j in jobs], ["https://a/2"])

    def test_the_newest_posting_comes_first(self):
        jobs, _ = c.select([job(url="https://a/1", posted="2026-07-01"),
                            job(url="https://a/2", company="B",
                                posted="2026-08-20")], c.FilterConfig())
        self.assertEqual([j.posted for j in jobs],
                         ["2026-08-20", "2026-07-01"])

    def test_us_is_filled_in_from_the_location_when_a_source_left_it(self):
        jobs, _ = c.select([job(location="Remote - US")], c.FilterConfig())
        self.assertEqual(jobs[0].us, "us")


# ==========================================================================
# The new/seen split
# ==========================================================================
class TestSplitNew(unittest.TestCase):

    def test_an_unseen_posting_is_new_and_stamped_today(self):
        j = job()
        fresh = c.split_new([j], {}, "2026-09-04")
        self.assertEqual(fresh, [j])
        self.assertEqual(j.first_seen, "2026-09-04")

    def test_a_known_posting_keeps_its_original_first_seen(self):
        j = job()
        seen = {c.job_key(j): {"first_seen": "2026-07-01"}}
        fresh = c.split_new([j], seen, "2026-09-04")
        self.assertEqual(fresh, [])
        self.assertEqual(j.first_seen, "2026-07-01")

    def test_everything_is_stamped_even_what_is_not_reported(self):
        # The archive gets the full list, so first_seen has to be on all of
        # it — narrowing the report is a presentation choice, not a filter
        # on what gets stored.
        old, new = job(url="https://a/1"), job(url="https://a/2", company="B")
        seen = {c.job_key(old): {"first_seen": "2026-07-01"}}
        fresh = c.split_new([old, new], seen, "2026-09-04")
        self.assertEqual([j.url for j in fresh], ["https://a/2"])
        self.assertEqual(old.first_seen, "2026-07-01")
        self.assertEqual(new.first_seen, "2026-09-04")


if __name__ == "__main__":
    unittest.main(verbosity=1)
