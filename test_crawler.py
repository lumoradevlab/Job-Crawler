#!/usr/bin/env python3
"""Tests for the grading logic — the part of the crawler that decides.

Every source normalises into one record and is judged by the same handful of
pure functions: us_status(), keep(), parse_salary(), relative_date() and the
regexes behind them. None of them touch the network, so all of it is testable
directly, and this file is where a regex tune gets to prove it didn't break a
case that used to work.

    python3 test_crawler.py           # all of it
    python3 test_crawler.py -v        # naming each case
    python3 test_crawler.py TestUsStatus

Stdlib only, like the crawler itself.
"""

import contextlib
import csv
import io
import json
import os
import shutil
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timezone

import crawler as c


def make_args(**over):
    """The subset of the CLI that keep() reads, defaulted to a plain run."""
    base = dict(no_filter=False, must=None, exclude=None, easy_apply_only=False,
                anywhere=False, strict_us=False, days=0, why=False)
    base.update(over)
    return Namespace(**base)


def make_cfg(**over):
    """A CrawlConfig for calling a source directly, with no CLI involved."""
    filters = c.FilterConfig(**{k: over.pop(k) for k in list(over)
                                if k in c.FilterConfig.__dataclass_fields__})
    base = dict(keywords=("Android Developer",), pages=1,
                location="United States")
    base.update(over)
    return c.CrawlConfig(filters=filters, **base)


def make_ctx(fetch=None, **over):
    """A RunContext whose Fetcher never reaches the network."""
    return c.RunContext(fetch=fetch or c.Fetcher(opener=_no_network), **over)


def _no_network(req, **kw):
    raise AssertionError(f"a test tried to reach the network: {req.full_url}")


def make_job(**over):
    """A record that passes every gate, so a test can spoil one field.

    Overrides that name a declared field set it; anything else is per-source
    scratch and lands in .ref, which is exactly where a Posting keeps it.
    """
    job = c.row("greenhouse", "Senior Android Engineer", "Acme",
                "Remote - US", "https://example.com/jobs/1", "2026-08-01")
    for key, value in over.items():
        if hasattr(job, key):
            setattr(job, key, value)
        else:
            job.ref[key] = value
    return job


# ==========================================================================
# us_status — the fiddliest gate, because boards state location in prose
# ==========================================================================
class TestUsStatus(unittest.TestCase):

    def assertStatus(self, cases):
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(c.us_status(text), want)

    def test_names_the_us(self):
        self.assertStatus([
            ("United States", "us"),
            ("USA", "us"),
            ("U.S.", "us"),
            ("U.S.A.", "us"),
            ("US only", "us"),
            ("US-based", "us"),
            ("Anywhere in the US", "us"),
            ("Nationwide", "us"),
            ("Remote - US", "us"),
            ("Remote, US", "us"),
        ])

    def test_parenthesised_and_reversed_us(self):
        # The two commonest ATS spellings. Both used to grade "unknown",
        # which under --strict-us silently dropped genuine US-remote roles.
        self.assertStatus([
            ("Remote (US)", "us"),
            ("Remote (USA)", "us"),
            ("Remote (United States)", "us"),
            ("US-Remote", "us"),
            ("USA - Remote", "us"),
            ("Remote — US", "us"),
        ])

    def test_state_names_and_abbreviations(self):
        self.assertStatus([
            ("San Francisco, CA", "us"),
            ("Austin, TX", "us"),
            ("New York, NY", "us"),
            ("Washington, DC", "us"),
            ("Chicago, Illinois", "us"),
            ("Remote - New Mexico", "us"),
        ])

    def test_worldwide_is_kept_not_dropped(self):
        # A US applicant qualifies for these, so they are not "no".
        self.assertStatus([
            ("Anywhere", "worldwide"),
            ("Worldwide", "worldwide"),
            ("Remote, Global", "worldwide"),
            ("Any location", "worldwide"),
        ])

    def test_fenced_to_another_region(self):
        self.assertStatus([
            ("London, UK", "no"),
            ("Berlin, Germany", "no"),
            ("Toronto, Canada", "no"),
            ("Cork, Ireland", "no"),
            ("Bangalore, India", "no"),
            ("EMEA", "no"),
            ("Remote (LATAM)", "no"),
        ])

    def test_british_places_that_read_as_us_states(self):
        # "Yorkshire" contains "york" and "Hampshire" contains "hampshire",
        # so a substring pass over STATES claims both for the US unless the
        # non-US check runs first.
        self.assertStatus([
            ("Yorkshire, England", "no"),
            ("Hampshire, UK", "no"),
            ("Manchester, England", "no"),
            ("Edinburgh, Scotland", "no"),
            ("Cardiff, Wales", "no"),
        ])

    def test_mexico_city_is_not_new_mexico(self):
        self.assertStatus([("Mexico City", "no"), ("Remote - New Mexico", "us")])

    def test_timezone_fences_rule_out_a_us_applicant(self):
        self.assertStatus([
            ("Remote (CET ±3)", "no"),
            ("Remote (GMT +2)", "no"),
        ])

    def test_timezone_fence_yields_to_an_explicit_us(self):
        # "Remote US (EST)" is a US posting quoting a US timezone.
        self.assertEqual(c.us_status("Remote US (CET overlap)"), "us")

    def test_silence_is_unknown_not_no(self):
        self.assertStatus([("", "unknown"), ("Remote", "unknown"),
                           ("Unspecified", "unknown")])

    def test_new_england_still_reads_as_us(self):
        # The non-US pass names England, but an explicit US signal outranks it.
        self.assertEqual(c.us_status("New England, MA"), "us")


class TestBuiltinUs(unittest.TestCase):
    """Built In writes countries as ISO-3, which us_status() cannot read."""

    def test_iso3_codes(self):
        self.assertEqual(c.builtin_us("Berlin, DEU"), "no")
        self.assertEqual(c.builtin_us("Amsterdam, NLD"), "no")
        self.assertEqual(c.builtin_us("New York, USA"), "us")

    def test_multi_country_qualifies_on_ours(self):
        self.assertEqual(c.builtin_us("USA, DEU"), "us")

    def test_falls_back_to_prose_when_no_code(self):
        self.assertEqual(c.builtin_us("Chicago, IL"), "us")
        self.assertEqual(c.builtin_us("Remote"), "unknown")


# ==========================================================================
# Workplace — the weakest signal in the crawl, so the one most worth pinning
# ==========================================================================
class TestWorkplaceRegexes(unittest.TestCase):

    def test_titles_that_name_an_office(self):
        for title in ["Android Engineer (Hybrid)", "Android Engineer - Onsite",
                      "Mobile Developer, In-Office", "Android Dev (On-Site)"]:
            with self.subTest(title=title):
                self.assertTrue(c.ONSITE.search(title))

    def test_hybrid_as_a_stack_is_not_an_office(self):
        # "Hybrid app developer" means React Native, not a hybrid office.
        for title in ["Hybrid App Developer", "Hybrid Mobile Engineer",
                      "Hybrid Application Developer"]:
            with self.subTest(title=title):
                self.assertFalse(c.ONSITE.search(title))

    def test_clean_remote_titles_are_untouched(self):
        for title in ["Senior Android Developer", "Remote Android Engineer",
                      "Kotlin Engineer"]:
            with self.subTest(title=title):
                self.assertFalse(c.ONSITE.search(title))

    def test_split_week_is_hybrid_however_worded(self):
        for title in ["3 days onsite 2 days remote", "Hybrid - 3 days",
                      "2x week in office", "4 days a week in the office"]:
            with self.subTest(title=title):
                self.assertTrue(c.HYBRID_SPLIT.search(title))

    def test_remote_strong_needs_a_commitment(self):
        for body in ["This role is fully remote", "we are a remote-first company",
                     "100% remote", "US-Remote", "open to remote",
                     "work from anywhere"]:
            with self.subTest(body=body):
                self.assertTrue(c.REMOTE_STRONG.search(body))

    def test_boilerplate_remote_does_not_count(self):
        # The whole reason REMOTE_STRONG exists: this sentence appears in
        # postings that are strictly on-site.
        for body in ["if the role can be performed remotely",
                     "we may consider remote for the right candidate"]:
            with self.subTest(body=body):
                self.assertFalse(c.REMOTE_STRONG.search(body))

    def test_onsite_strong_needs_a_commitment_too(self):
        for body in ["This position is onsite", "3 days a week in the office",
                     "Location: Austin - hybrid", "required to work on-site"]:
            with self.subTest(body=body):
                self.assertTrue(c.ONSITE_STRONG.search(body))

    def test_incidental_onsite_does_not_count(self):
        for body in ["onsite interviews will follow",
                     "you will meet customers onsite twice a year"]:
            with self.subTest(body=body):
                self.assertFalse(c.ONSITE_STRONG.search(body))


class TestRelevanceGate(unittest.TestCase):

    def assertRelevant(self, title, want):
        got = bool(c.RELEVANT.search(title) and c.ROLE.search(title))
        self.assertEqual(got, want, title)

    def test_mobile_engineering_titles_pass(self):
        for t in ["Android Engineer", "Senior Kotlin Developer",
                  "Mobile Software Engineer", "React Native Developer",
                  "iOS/Android Engineer", "Flutter Developer",
                  "Jetpack Compose Developer"]:
            self.assertRelevant(t, True)

    def test_non_mobile_and_non_engineering_titles_fail(self):
        for t in ["Backend Engineer", "Product Manager, Mobile",
                  "Android", "Mobile Marketing Lead", "Data Scientist"]:
            self.assertRelevant(t, False)


# ==========================================================================
# keep() — the single gate, exercised end to end
# ==========================================================================
class TestKeep(unittest.TestCase):

    def test_a_clean_remote_us_android_job_passes(self):
        self.assertTrue(c.keep(make_job(), make_args()))

    def test_non_mobile_title_is_dropped(self):
        self.assertFalse(c.keep(make_job(title="Backend Engineer"), make_args()))

    def test_no_filter_keeps_it_anyway(self):
        self.assertTrue(c.keep(make_job(title="Backend Engineer"),
                               make_args(no_filter=True)))

    def test_free_text_sources_widen_the_gate_with_match_text(self):
        # HN buries the role in the body, so match_text stands in for a title.
        job = make_job(title="Acme Corp | SF | full-time",
                       match_text="We are hiring an Android engineer")
        self.assertTrue(c.keep(job, make_args()))

    def test_not_remote_is_dropped(self):
        self.assertFalse(c.keep(make_job(remote=False), make_args()))

    def test_onsite_title_outranks_the_boards_remote_flag(self):
        # LinkedIn's shape: its f_WT=2 filter leaks hybrid roles, its guest
        # pages carry no workplace field, and its locations never say
        # "remote" — so the title is the only signal there is.
        job = make_job(source="linkedin", title="Android Engineer (Hybrid)",
                       location="United States", remote=True)
        self.assertFalse(c.keep(job, make_args()))

    def test_onsite_is_forgiven_when_remote_is_also_offered(self):
        job = make_job(title="Android Engineer (Remote or Hybrid)", remote=True)
        self.assertTrue(c.keep(job, make_args()))

    def test_a_boards_workplace_tag_may_offer_both(self):
        # Built In's shape: the workplace tag is prepended to the place, and
        # "In-Office or Remote" is the board saying remote is on the table.
        # Judging the title alone would throw away a fifth of its results.
        job = make_job(source="builtin", title="Senior Android Engineer",
                       location="In-Office or Remote Dallas, TX, USA")
        self.assertTrue(c.keep(job, make_args()))
        job = make_job(source="builtin", title="Mobile Engineer",
                       location="Hybrid Dallas, TX, USA")
        self.assertFalse(c.keep(job, make_args()))

    def test_split_week_is_dropped_even_though_it_says_remote(self):
        job = make_job(title="Android Engineer - 3 days onsite 2 days remote")
        self.assertFalse(c.keep(job, make_args()))

    def test_region_fenced_postings_are_dropped(self):
        self.assertFalse(c.keep(make_job(location="Berlin, Germany", us=None),
                                make_args()))

    def test_worldwide_is_kept_by_default(self):
        self.assertTrue(c.keep(make_job(location="Anywhere", us=None),
                               make_args()))

    def test_strict_us_drops_worldwide_and_unlabelled(self):
        args = make_args(strict_us=True)
        self.assertFalse(c.keep(make_job(location="Anywhere", us=None), args))
        self.assertFalse(c.keep(make_job(location="Remote", us=None), args))
        self.assertTrue(c.keep(make_job(location="Remote - US", us=None), args))

    def test_anywhere_turns_the_us_gate_off(self):
        job = make_job(location="Berlin, Germany", us=None)
        self.assertTrue(c.keep(job, make_args(anywhere=True)))

    def test_precomputed_us_beats_the_location_string(self):
        # Ashby and friends state the country outright; that must win over
        # whatever the free-text label happens to say.
        job = make_job(location="London office", us="us")
        self.assertTrue(c.keep(job, make_args(strict_us=True)))

    def test_exclude_matches_on_title_only(self):
        args = make_args(exclude=["manager"])
        self.assertFalse(c.keep(make_job(title="Android Engineering Manager"), args))
        self.assertTrue(c.keep(make_job(description="reports to a manager"), args))

    def test_must_matches_title_or_description(self):
        args = make_args(must=["kotlin"])
        self.assertFalse(c.keep(make_job(), args))
        self.assertTrue(c.keep(make_job(description="5 years of Kotlin"), args))

    def test_easy_apply_only(self):
        args = make_args(easy_apply_only=True)
        self.assertFalse(c.keep(make_job(easy_apply="no"), args))
        self.assertTrue(c.keep(make_job(easy_apply="yes"), args))

    def test_date_window(self):
        recent = datetime.now().strftime("%Y-%m-%d")
        self.assertTrue(c.keep(make_job(posted=recent), make_args(days=30)))
        self.assertFalse(c.keep(make_job(posted="2020-01-01"), make_args(days=30)))

    def test_undated_postings_survive_the_window(self):
        # ATS boards keep roles live far longer than they date them; dropping
        # an undated posting would lose the highest-signal source.
        self.assertTrue(c.keep(make_job(posted=""), make_args(days=30)))
        self.assertTrue(c.keep(make_job(posted="not a date"), make_args(days=30)))


# ==========================================================================
# --why: the gate explaining itself
# ==========================================================================
class TestRejection(unittest.TestCase):
    """Every reason must come from the branch that actually fired, which is
    only true while keep() and rejection() stay the same function."""

    def rule(self, job, args=None):
        why = c.rejection(job, args or make_args())
        return why.split(":", 1)[0] if why else None

    def test_a_passing_job_has_no_reason(self):
        self.assertIsNone(c.rejection(make_job(), make_args()))

    def test_each_rule_names_itself(self):
        cases = [
            ("not-mobile", make_job(title="Backend Engineer"), make_args()),
            ("must", make_job(), make_args(must=["kotlin"])),
            ("exclude", make_job(title="Android Engineering Manager"),
             make_args(exclude=["manager"])),
            ("easy-apply", make_job(easy_apply="no"),
             make_args(easy_apply_only=True)),
            ("not-remote", make_job(remote=False), make_args()),
            ("hybrid-split", make_job(title="Android Engineer, 3 days onsite"),
             make_args()),
            ("onsite", make_job(source="linkedin", location="United States",
                                title="Android Engineer (Hybrid)"), make_args()),
            ("region", make_job(location="Berlin, Germany", us=None),
             make_args()),
            ("not-us", make_job(location="Anywhere", us=None),
             make_args(strict_us=True)),
            ("too-old", make_job(posted="2020-01-01"), make_args(days=30)),
        ]
        for want, job, args in cases:
            with self.subTest(rule=want):
                self.assertEqual(self.rule(job, args), want)

    def test_a_reason_carries_a_detail_after_its_category(self):
        # The category groups the summary; the detail is what makes one row
        # in the CSV worth reading.
        why = c.rejection(make_job(location="Berlin, Germany", us=None),
                          make_args())
        self.assertIn(": ", why)
        self.assertIn("Berlin", why)

    def test_the_first_failing_rule_is_the_one_reported(self):
        # A posting can break several rules; the reason names the one that
        # stopped it, in gate order.
        job = make_job(title="Backend Engineer", remote=False,
                       location="Berlin, Germany", us=None)
        self.assertEqual(self.rule(job), "not-mobile")

    def test_keep_is_exactly_the_shadow_of_rejection(self):
        jobs = [make_job(), make_job(title="Backend Engineer"),
                make_job(remote=False), make_job(location="Berlin", us=None),
                make_job(posted="2020-01-01")]
        for args in (make_args(), make_args(days=30), make_args(strict_us=True),
                     make_args(anywhere=True), make_args(no_filter=True)):
            for job in jobs:
                with self.subTest(job=job.title, args=vars(args)):
                    self.assertEqual(c.keep(job, args),
                                     c.rejection(job, args) is None)


class TestRelevant(unittest.TestCase):
    """The title gate the sources apply for themselves, before paying for a
    detail fetch — and the ledger that keeps those drops visible to --why."""

    def setUp(self):
        self.report = c.Reporter(quiet=True)

    def test_it_is_the_same_gate_keep_applies(self):
        self.assertTrue(c.relevant("Android Engineer", make_args()))
        self.assertFalse(c.relevant("Account Executive", make_args()))

    def test_no_filter_lets_everything_through(self):
        self.assertTrue(c.relevant("Account Executive",
                                   make_args(no_filter=True)))

    def test_nothing_is_recorded_unless_why_asked(self):
        c.relevant("Account Executive", make_args(), "ashby", self.report)
        self.assertEqual(self.report.skips, [])

    def test_why_records_the_title_against_its_source(self):
        # The source passes its own name rather than the gate reading a
        # mutable "who is running now" field off shared state.
        args = make_args(why=True)
        c.relevant("Account Executive", args, "ashby", self.report)
        c.relevant("Android Engineer", args, "ashby", self.report)  # kept
        self.assertEqual(self.report.skips, [("ashby", "Account Executive")])


class TestReportRejections(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def report(self, rejected, skipped=()):
        base = os.path.join(self.dir, "run")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            c.report_rejections(rejected, base, skipped)
        with open(base + "_rejected.csv", encoding="utf-8") as fh:
            return list(csv.DictReader(fh)), buf.getvalue()

    def test_writes_a_row_per_rejection_with_its_reason(self):
        rows, _ = self.report([
            (make_job(title="Android Engineer", location="Berlin, Germany"),
             "region: fenced outside the US (Berlin, Germany)"),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Android Engineer")
        self.assertTrue(rows[0]["reason"].startswith("region:"))

    def test_source_dropped_titles_are_included(self):
        # Otherwise the rule that rejects the most postings is invisible.
        rows, out = self.report([], [("greenhouse", "Account Executive")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "greenhouse")
        self.assertIn("not-mobile", rows[0]["reason"])
        self.assertIn("1 of them by a source's own title gate", out)

    def test_the_summary_groups_on_the_category_and_ranks_by_size(self):
        rejected = [(make_job(source="linkedin"), "onsite: says 'hybrid'")] * 3
        rejected += [(make_job(source="ashby"), "region: fenced outside the US")]
        _, out = self.report(rejected)
        self.assertIn("onsite", out)
        self.assertIn("linkedin 3", out)
        self.assertLess(out.index("onsite"), out.index("region"))

    def test_every_column_is_written_even_when_a_field_is_missing(self):
        rows, _ = self.report([({"source": "hn", "title": "x"}, "region: ?")])
        self.assertEqual(set(rows[0]), set(c.REJECTED_COLUMNS))


# ==========================================================================
# Salary
# ==========================================================================
class TestParseSalary(unittest.TestCase):

    def test_ranges_and_single_figures(self):
        self.assertEqual(c.parse_salary("$100k - $120k"), (100000.0, 120000.0))
        self.assertEqual(c.parse_salary("$100,000 to $130,000"), (100000.0, 130000.0))
        self.assertEqual(c.parse_salary("$150,000"), (150000.0, None))
        self.assertEqual(c.parse_salary("$90k"), (90000.0, None))

    def test_hourly_rates_are_annualised_at_2080h(self):
        self.assertEqual(c.parse_salary("$80 per hour"), (166400.0, None))
        self.assertEqual(c.parse_salary("$45/hr"), (93600.0, None))
        self.assertEqual(c.parse_salary("$65 an hour"), (135200.0, None))

    def test_rate_fragments_are_treated_as_unstated(self):
        # A bare "$120" is a fragment, not a salary.
        self.assertEqual(c.parse_salary("$120"), (None, None))

    def test_no_figure_at_all(self):
        for text in ["", None, "competitive", "DOE", "£90,000"]:
            with self.subTest(text=text):
                self.assertEqual(c.parse_salary(text), (None, None))


class TestAnnualise(unittest.TestCase):
    """USAJOBS states pay per hour as often as per year, and says which in
    RateIntervalCode — so the figure has to be normalised before --min-salary
    can compare it against anything else."""

    def test_hourly_codes_are_annualised(self):
        self.assertEqual(c.annualise(80, "PH"), 166400)
        self.assertEqual(c.annualise(80, "Per Hour"), 166400)

    def test_annual_codes_pass_through(self):
        self.assertEqual(c.annualise(150000, "PA"), 150000)
        self.assertEqual(c.annualise(150000, ""), 150000)

    def test_missing_figures_stay_missing(self):
        self.assertIsNone(c.annualise(None, "PH"))


# ==========================================================================
# Dates
# ==========================================================================
class TestRelativeDate(unittest.TestCase):
    """Built In posts no timestamps at all; Google Jobs posts "3 days ago"."""

    TODAY = datetime(2026, 8, 31)

    def check(self, text, want):
        self.assertEqual(c.relative_date(text, self.TODAY), want)

    def test_relative_days(self):
        self.check("Reposted 3 Days Ago", "2026-08-28")
        self.check("5 Days Ago", "2026-08-26")
        self.check("30+ Days Ago", "2026-08-01")
        self.check("1 Day Ago", "2026-08-30")

    def test_today_and_yesterday(self):
        self.check("Today", "2026-08-31")
        self.check("Just Posted", "2026-08-31")
        self.check("2 Hours Ago", "2026-08-31")
        self.check("45 Minutes Ago", "2026-08-31")
        self.check("Yesterday", "2026-08-30")

    def test_unparseable_is_empty_not_wrong(self):
        self.check("", "")
        self.check("a while back", "")


class TestCatchupDays(unittest.TestCase):
    """After a 60-day sweep the next run only needs the days since."""

    def state(self, last_run):
        return {c.META: {"last_run": last_run}}

    def test_no_history_asks_for_the_whole_window(self):
        self.assertEqual(c.catchup_days({}, 60, "2026-08-31"), 60)

    def test_gap_plus_two_days_of_slack(self):
        self.assertEqual(c.catchup_days(self.state("2026-08-26"), 60, "2026-08-31"), 7)

    def test_same_day_rerun_still_asks_for_something(self):
        self.assertEqual(c.catchup_days(self.state("2026-08-31"), 60, "2026-08-31"), 2)

    def test_never_widens_past_the_requested_window(self):
        self.assertEqual(c.catchup_days(self.state("2020-01-01"), 60, "2026-08-31"), 60)

    def test_a_corrupt_timestamp_falls_back_to_the_full_window(self):
        self.assertEqual(c.catchup_days(self.state("nonsense"), 60, "2026-08-31"), 60)


class TestPerSourceCatchup(unittest.TestCase):
    """A source that was down must be asked for the whole time it was down.

    The run-wide last_run advanced even when a source had failed, so the next
    morning asked for one day and everything the broken source would have
    returned had already fallen outside the window. Nothing ever went back
    for it.
    """

    def state(self, **per_source):
        return {c.META: {"last_run": "2026-08-30", "sources": dict(per_source)}}

    def test_a_healthy_source_only_asks_for_the_gap(self):
        st = self.state(greenhouse="2026-08-30")
        self.assertEqual(
            c.catchup_days(st, 60, "2026-08-31", ["greenhouse"]), 3)

    def test_a_source_that_has_been_down_asks_for_the_whole_outage(self):
        st = self.state(greenhouse="2026-08-30", ashby="2026-08-24")
        self.assertEqual(
            c.catchup_days(st, 60, "2026-08-31", ["ashby"]), 9)

    def test_the_window_covers_the_oldest_source_in_the_run(self):
        # One window is asked of every source, so it has to be wide enough
        # for the one furthest behind.
        st = self.state(greenhouse="2026-08-30", ashby="2026-08-24")
        self.assertEqual(
            c.catchup_days(st, 60, "2026-08-31", ["greenhouse", "ashby"]), 9)

    def test_a_source_never_recorded_asks_for_everything(self):
        st = self.state(greenhouse="2026-08-30")
        self.assertEqual(c.catchup_days(st, 60, "2026-08-31", ["lever"]), 60)

    def test_old_state_without_per_source_stamps_still_works(self):
        # State written before this bookkeeping existed has no "sources" key.
        old = {c.META: {"last_run": "2026-08-30"}}
        self.assertEqual(c.catchup_days(old, 60, "2026-08-31", ["ashby"]), 60)
        self.assertEqual(c.catchup_days(old, 60, "2026-08-31"), 3)

    def test_only_the_sources_that_worked_advance(self):
        st = self.state(greenhouse="2026-08-24", ashby="2026-08-24")
        c.record_run(st, "2026-08-31", 60, {"greenhouse"})
        self.assertEqual(st[c.META]["sources"],
                         {"greenhouse": "2026-08-31", "ashby": "2026-08-24"})
        # ashby is still nine days behind and will be asked for nine days.
        self.assertEqual(c.catchup_days(st, 60, "2026-08-31", ["ashby"]), 9)


# ==========================================================================
# Identity and de-duplication
# ==========================================================================
class TestJobKey(unittest.TestCase):

    def test_url_is_the_identity(self):
        self.assertEqual(c.job_key(make_job(url="https://x.com/j/1")),
                         "https://x.com/j/1")

    def test_trailing_slash_is_not_a_different_job(self):
        self.assertEqual(c.job_key(make_job(url="https://x.com/j/1/")),
                         c.job_key(make_job(url="https://x.com/j/1")))

    def test_query_string_is_kept(self):
        # Several boards carry the job id there (…/jobs/?gh_jid=4916795),
        # so stripping it would merge unrelated postings.
        a = c.job_key(make_job(url="https://x.com/jobs/?gh_jid=1"))
        b = c.job_key(make_job(url="https://x.com/jobs/?gh_jid=2"))
        self.assertNotEqual(a, b)

    def test_falls_back_to_source_title_company(self):
        key = c.job_key(make_job(url="", title="Android Eng", company="Acme"))
        self.assertEqual(key, "greenhouse|android eng|acme")


# ==========================================================================
# The archive — the only full record of what has been matched
# ==========================================================================
class TestArchive(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "run_archive.jsonl")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_missing_archive_is_empty_not_an_error(self):
        self.assertEqual(c.load_archive(self.path), [])

    def test_what_is_appended_comes_back(self):
        c.append_archive(self.path, [make_job(url="https://x/1")])
        got = c.load_archive(self.path)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].title, "Senior Android Engineer")

    def test_appending_never_rewrites_what_is_there(self):
        c.append_archive(self.path, [make_job(url="https://x/1")])
        c.append_archive(self.path, [make_job(url="https://x/2")])
        self.assertEqual([j.url for j in c.load_archive(self.path)],
                         ["https://x/1", "https://x/2"])

    def test_a_posting_already_archived_is_not_added_twice(self):
        job = make_job(url="https://x/1")
        self.assertEqual(c.append_archive(self.path, [job]), 1)
        self.assertEqual(c.append_archive(self.path, [job]), 0)
        self.assertEqual(len(c.load_archive(self.path)), 1)

    def test_a_half_written_line_does_not_lose_the_file(self):
        # An interrupted run can leave a partial line; the rest must survive.
        c.append_archive(self.path, [make_job(url="https://x/1")])
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"url": "https://x/2", "ti\n')
        c.append_archive(self.path, [make_job(url="https://x/3")])
        self.assertEqual([j.url for j in c.load_archive(self.path)],
                         ["https://x/1", "https://x/3"])

    def test_scratch_fields_cannot_reach_the_archive(self):
        # Per-source scratch lives in .ref, which as_record() never emits —
        # so this holds for a field no one has invented yet, where the old
        # hand-written BOOKKEEPING tuple only covered the five it listed.
        job = make_job(url="https://x/1", match_text="body", gh_token="stripe")
        c.append_archive(self.path, [job])
        with open(self.path, encoding="utf-8") as fh:
            written = json.loads(fh.readline())
        self.assertNotIn("match_text", written)
        self.assertNotIn("gh_token", written)
        self.assertNotIn("ref", written)
        self.assertIn("title", written)

    def test_the_archive_keeps_the_fields_the_seen_state_throws_away(self):
        # The point of the file: the seen-state remembers a title, a company
        # and a date, and nothing that makes a posting worth reading later.
        c.append_archive(self.path, [make_job(
            url="https://x/1", location="Remote - US", salary_min=180000)])
        stored, = c.load_archive(self.path)
        for field in ("location", "salary_min", "posted", "source", "url"):
            self.assertTrue(getattr(stored, field),
                            f"{field} did not survive the archive")


# ==========================================================================
# De-duplication across sources
# ==========================================================================
class TestDedupeKey(unittest.TestCase):

    def key(self, title, company="Acme"):
        return c.dedupe_key(make_job(title=title, company=company))

    def same(self, a, b, company_a="Acme", company_b="Acme"):
        self.assertEqual(self.key(a, company_a), self.key(b, company_b))

    def different(self, a, b):
        self.assertNotEqual(self.key(a), self.key(b))

    def test_punctuation_between_sources_is_not_a_different_job(self):
        self.same("Mobile Engineer II (Android)", "Mobile Engineer II, Android")
        self.same("Senior, Software Engineer - Android",
                  "Senior, Software Engineer- Android")

    def test_a_legal_suffix_is_not_a_different_company(self):
        self.same("Android Engineer", "Android Engineer", "Reddit, Inc.", "Reddit")
        self.same("Android Engineer", "Android Engineer", "Expedia Group",
                  "Expedia")

    def test_a_trailing_workplace_or_location_is_dropped(self):
        for spelling in ["Android Engineer (Remote)", "Android Engineer - Remote",
                         "Android Engineer, US", "Android Engineer (Remote) - US",
                         "Android Engineer (m/f/d)"]:
            with self.subTest(title=spelling):
                self.same(spelling, "Android Engineer")

    def test_seniority_is_a_different_job(self):
        self.different("Senior Android Engineer", "Android Engineer")
        self.different("Staff Android Engineer", "Android Engineer")

    def test_a_team_in_brackets_is_a_different_job(self):
        self.different("Android Engineer (Payments)", "Android Engineer")

    def test_employment_type_is_a_different_posting(self):
        self.different("Android Engineer - Contract", "Android Engineer")

    def test_a_word_merely_ending_in_a_suffix_survives(self):
        # Without a word boundary the "us" rule eats the tail of "Focus".
        self.assertEqual(self.key("Software Engineer, Focus")[0],
                         "software engineer focus")

    def test_a_title_that_is_entirely_a_suffix_falls_back(self):
        self.assertEqual(self.key("Remote")[0], "remote")

    def test_a_company_that_normalises_to_nothing_falls_back(self):
        # Otherwise every company called "Group" collides with every other.
        self.assertEqual(self.key("Android Engineer", "Group")[1], "group")

    def test_entities_are_decoded_before_comparing(self):
        self.same("Android Engineer", "Android Engineer", "Ben &amp; Co", "Ben &")


# ==========================================================================
# Parsing helpers
# ==========================================================================
class TestStripTags(unittest.TestCase):

    def test_breaks_become_newlines(self):
        self.assertEqual(c.strip_tags("a<br>b"), "a\nb")

    def test_double_encoded_entities(self):
        # HN and some RSS feeds encode their entities twice.
        self.assertEqual(c.strip_tags("R&amp;amp;D"), "R&D")
        self.assertEqual(c.strip_tags("a &amp; b"), "a & b")

    def test_tags_are_removed_and_whitespace_collapsed(self):
        self.assertEqual(c.strip_tags("<p>hello   <b>world</b></p>"),
                         "hello world")

    def test_a_body_that_is_escaped_markup_is_still_stripped(self):
        # Greenhouse escapes its whole description, so the markup arrives as
        # text about markup. Stripping tags first finds none, and the old
        # unescape afterwards left literal <h2> in the CSV.
        # A heading does not break the line here, exactly as a real <h2>
        # does not — only </p>, </li>, </ul> and </div> do. The point of the
        # case is that no markup survives into the text.
        self.assertEqual(
            c.strip_tags("&lt;h2&gt;Who we are&lt;/h2&gt;&lt;p&gt;Hi&lt;/p&gt;"),
            "Who we are Hi")
        self.assertEqual(c.strip_tags("<h2>Who we are</h2><p>Hi</p>"),
                         "Who we are Hi")

    def test_escaped_generics_are_not_mistaken_for_markup(self):
        # The reason escaped markup is handled by an allowlist of tag names
        # rather than by unescaping everything first: these are Android job
        # descriptions, and unescaping first turns this into "Flow >".
        self.assertEqual(
            c.strip_tags("Flow&lt;List&lt;User&gt;&gt; and LiveData&lt;T&gt;"),
            "Flow<List<User>> and LiveData<T>")

    def test_an_escaped_tag_may_carry_an_entity_in_an_attribute(self):
        self.assertEqual(c.strip_tags('&lt;a href="a&amp;b"&gt;text&lt;/a&gt;'),
                         "text")

    def test_comparisons_in_prose_survive(self):
        self.assertEqual(c.strip_tags("scale 3 &lt; 5 and A &amp; B"),
                         "scale 3 < 5 and A & B")


class TestAshbyPlaces(unittest.TestCase):
    """A posting headquartered in New York but open to Remote (US) carries
    that only in secondaryLocations."""

    def test_primary_and_secondary_are_both_read(self):
        job = {
            "location": "New York",
            "address": {"postalAddress": {"addressCountry": "United States"}},
            "secondaryLocations": [
                {"location": "Remote (US)",
                 "address": {"postalAddress": {"addressCountry": "United States"}}},
            ],
        }
        label, countries = c.ashby_places(job)
        self.assertEqual(label, "New York / Remote (US)")
        self.assertEqual(countries, {"united states"})

    def test_missing_address_is_not_an_error(self):
        label, countries = c.ashby_places({"location": "Remote"})
        self.assertEqual(label, "Remote")
        self.assertEqual(countries, set())

    def test_duplicate_labels_collapse(self):
        job = {"location": "Remote",
               "secondaryLocations": [{"location": "Remote"}]}
        self.assertEqual(c.ashby_places(job)[0], "Remote")


class TestJobCardParser(unittest.TestCase):
    """The LinkedIn guest search returns an HTML fragment, not JSON."""

    CARD = """
    <li><div class="base-card" data-entity-urn="urn:li:jobPosting:4055123456">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/android-engineer-at-acme-4055123456?refId=xyz">
        <span class="sr-only">Android Engineer</span></a>
      <h3 class="base-search-card__title">Android Engineer</h3>
      <h4 class="base-search-card__subtitle"><a>Acme Corp</a></h4>
      <span class="job-search-card__location">United States (Remote)</span>
      <time class="job-search-card__listdate" datetime="2026-08-20">1 week ago</time>
    </div></li>
    """

    def parse(self, html):
        p = c.JobCardParser()
        p.feed(html)
        p.close()
        return p.jobs

    def test_reads_every_field_off_a_card(self):
        job, = self.parse(self.CARD)
        self.assertEqual(job["job_id"], "4055123456")
        self.assertEqual(job["title"], "Android Engineer")
        self.assertEqual(job["company"], "Acme Corp")
        self.assertEqual(job["location"], "United States (Remote)")
        self.assertEqual(job["posted"], "2026-08-20")

    def test_tracking_parameters_are_stripped_from_the_link(self):
        job, = self.parse(self.CARD)
        self.assertNotIn("?", job["url"])
        self.assertTrue(job["url"].endswith("4055123456"))

    def test_several_cards_in_one_fragment(self):
        jobs = self.parse(self.CARD + self.CARD.replace("4055123456", "999")
                          .replace("Android Engineer", "Kotlin Developer"))
        self.assertEqual([j["job_id"] for j in jobs], ["4055123456", "999"])

    def test_a_card_with_no_title_is_discarded(self):
        self.assertEqual(self.parse(
            '<div data-entity-urn="urn:li:jobPosting:1"></div>'), [])

    def test_missing_link_falls_back_to_the_canonical_url(self):
        html = """<div data-entity-urn="urn:li:jobPosting:777">
                  <h3 class="base-search-card__title">Android Engineer</h3></div>"""
        job, = self.parse(html)
        self.assertEqual(job["url"], "https://www.linkedin.com/jobs/view/777")
        self.assertEqual(job["company"], "")


class TestNextData(unittest.TestCase):

    def test_pulls_pageprops_out_of_a_nextjs_page(self):
        html = ('<script id="__NEXT_DATA__" type="application/json">'
                '{"props":{"pageProps":{"arcJobs":[{"title":"Android"}]}}}'
                '</script>')
        self.assertEqual(c.next_data(html),
                         {"arcJobs": [{"title": "Android"}]})

    def test_a_page_without_the_payload_is_empty_not_an_error(self):
        self.assertEqual(c.next_data("<html>nothing here</html>"), {})
        self.assertEqual(c.next_data(
            '<script id="__NEXT_DATA__">not json</script>'), {})


# ==========================================================================
# Google Jobs via SerpApi
# ==========================================================================
class TestGoogleSalary(unittest.TestCase):
    """Google names the interval in words and often omits the currency."""

    def test_a_year(self):
        self.assertEqual(c.google_salary("84K–96K a year"), (84000, 96000))
        self.assertEqual(c.google_salary("$100,000–$120,000 a year"),
                         (100000, 120000))

    def test_an_hour_is_annualised(self):
        self.assertEqual(c.google_salary("$40 an hour"), (83200, None))
        self.assertEqual(c.google_salary("$25–$30 an hour"), (52000, 62400))

    def test_a_month_and_a_week(self):
        self.assertEqual(c.google_salary("$5,000 a month"), (60000, None))
        self.assertEqual(c.google_salary("$2,000 a week"), (104000, None))

    def test_no_figure(self):
        for text in ["", None, "competitive", "5 years of experience"]:
            with self.subTest(text=text):
                self.assertEqual(c.google_salary(text), (None, None))


class TestCrawlSerpApi(unittest.TestCase):
    """Driven by a trimmed copy of a real google_jobs response."""

    SAMPLE = {
        "search_metadata": {"status": "Success"},
        "jobs_results": [
            {
                "title": "Mobile Developer - iOS & Android (Remote)",
                "company_name": "Confer",
                "location": "Anywhere",
                "via": "CareerBuilder",
                "description": "We are looking for a mobile developer...",
                "share_link": "https://www.google.com/search?ibp=htl;jobs&q=x",
                "source_link": "https://www.careerbuilder.com/job-details/8b102674",
                "extensions": ["Work from home", "Full-time",
                               "No degree mentioned"],
                "detected_extensions": {"work_from_home": True,
                                        "schedule_type": "Full-time"},
                "apply_options": [{"title": "CareerBuilder",
                                   "link": "https://www.careerbuilder.com/apply"}],
            },
            {
                "title": "Remote Android Developer",
                "company_name": "DataAnnotation",
                "location": "Anywhere",
                "via": "Talents By Vaia",
                "description": "A cutting-edge AI development company...",
                "source_link": "https://talents.vaia.com/companies/x/99759093/",
                "extensions": ["84K–96K a year", "Work from home", "Full-time"],
                "detected_extensions": {"salary": "84K–96K a year",
                                        "work_from_home": True,
                                        "posted_at": "3 days ago"},
                "apply_options": [{"title": "Talents By Vaia",
                                   "link": "https://talents.vaia.com/apply"}],
            },
            {
                "title": "Senior Account Executive",
                "company_name": "Acme",
                "location": "Austin, TX",
                "detected_extensions": {"work_from_home": True},
            },
        ],
    }

    def setUp(self):
        self.urls = []
        os.environ["SERPAPI_KEY"] = "test-key"

    def tearDown(self):
        os.environ.pop("SERPAPI_KEY", None)

    def crawl(self, payloads, **over):
        """Run the source against recorded payloads, with no network at all.

        This used to work by reassigning the module-global fetch_json and
        patching time.sleep. Both were symptoms: HTTP was a global the source
        reached for, and pacing was a sleep inside the paging loop. Now the
        source is handed a fetcher, so a test simply hands it a different one
        — no globals touched, and nothing to restore in tearDown.
        """
        queue = list(payloads)

        class Recording:
            urls = self.urls

            def get_json(_self, url, **kw):
                self.urls.append(url)
                return queue.pop(0) if queue else None

        with contextlib.redirect_stdout(io.StringIO()) as buf:
            jobs = c.crawl_serpapi(make_cfg(**over), make_ctx(fetch=Recording()))
        return jobs, buf.getvalue()

    def test_the_mobile_roles_are_kept_and_the_others_are_not(self):
        jobs, _ = self.crawl([self.SAMPLE])
        self.assertEqual([j.title for j in jobs],
                         ["Mobile Developer - iOS & Android (Remote)",
                          "Remote Android Developer"])

    def test_work_from_home_is_a_real_remote_flag(self):
        # The only source that states this structurally instead of leaving it
        # to be read out of prose.
        jobs, _ = self.crawl([self.SAMPLE])
        self.assertTrue(all(j.remote for j in jobs))

    def test_the_posting_link_beats_the_google_redirect(self):
        jobs, _ = self.crawl([self.SAMPLE])
        self.assertEqual(jobs[0].url,
                         "https://www.careerbuilder.com/job-details/8b102674")
        self.assertNotIn("google.com", jobs[0].url)
        self.assertEqual(jobs[0].apply_url,
                         "https://www.careerbuilder.com/apply")

    def test_salary_and_date_come_off_detected_extensions(self):
        jobs, _ = self.crawl([self.SAMPLE])
        self.assertEqual((jobs[1].salary_min, jobs[1].salary_max),
                         (84000, 96000))
        self.assertEqual(jobs[1].salary_currency, "USD")
        self.assertTrue(jobs[1].posted)             # "3 days ago" resolved

    def test_anywhere_survives_the_gate_but_not_strict_us(self):
        jobs, _ = self.crawl([self.SAMPLE])
        self.assertEqual(c.us_status(jobs[0].location), "worldwide")
        self.assertTrue(c.keep(jobs[0], make_args()))
        self.assertFalse(c.keep(jobs[0], make_args(strict_us=True)))

    def test_a_missing_key_explains_itself_and_spends_nothing(self):
        os.environ.pop("SERPAPI_KEY")
        jobs, out = self.crawl([self.SAMPLE])
        self.assertEqual(jobs, [])
        self.assertEqual(self.urls, [])
        self.assertIn("serpapi.com/users/sign_up", out)

    def test_an_error_payload_stops_before_spending_more(self):
        # Quota exhaustion and a bad key both arrive as a 200 with "error".
        jobs, _ = self.crawl([{"error": "Your account has run out of searches"}],
                             keywords=["A Developer", "B Developer"])
        self.assertEqual(jobs, [])
        self.assertEqual(len(self.urls), 1)

    def test_pages_are_capped_however_many_are_asked_for(self):
        page = dict(self.SAMPLE, serpapi_pagination={"next_page_token": "t"})
        self.crawl([page] * 10, pages=99)
        self.assertEqual(len(self.urls), c.SERPAPI_MAX_PAGES)

    def test_pagination_follows_the_token_and_stops_without_one(self):
        first = dict(self.SAMPLE, serpapi_pagination={"next_page_token": "abc"})
        self.crawl([first, self.SAMPLE], pages=3)
        self.assertEqual(len(self.urls), 2)
        self.assertIn("next_page_token=abc", self.urls[1])
        self.assertNotIn("next_page_token", self.urls[0])

    def test_the_search_is_scoped_to_the_requested_location(self):
        self.crawl([self.SAMPLE])
        self.assertIn("engine=google_jobs", self.urls[0])
        self.assertIn("location=United+States", self.urls[0])

    def test_anywhere_drops_the_location_rather_than_erroring(self):
        # SerpApi rejects a location it cannot resolve, and "Worldwide" is
        # not one of its places.
        self.crawl([self.SAMPLE], location="Worldwide")
        self.assertNotIn("location=", self.urls[0])


# ==========================================================================
# --discover: growing the ATS board lists
# ==========================================================================
class TestSlugCandidates(unittest.TestCase):

    def test_a_one_word_name_is_its_own_slug(self):
        self.assertEqual(c.slug_candidates("Klaviyo"), ["klaviyo"])

    def test_legal_suffixes_are_dropped(self):
        for name in ["Scribd, Inc.", "Scribd LLC", "Scribd Ltd.", "Scribd GmbH"]:
            with self.subTest(name=name):
                self.assertEqual(c.slug_candidates(name), ["scribd"])

    def test_both_spellings_of_a_two_word_name_are_tried(self):
        # Nothing anywhere says which one an ATS uses, so all three are probed.
        self.assertEqual(c.slug_candidates("Epic Games"),
                         ["epicgames", "epic-games", "epic"])

    def test_only_a_two_word_name_gives_up_its_head_word(self):
        # "Bank of America" must not probe "bank".
        self.assertEqual(c.slug_candidates("Bank of America"),
                         ["bankofamerica", "bank-of-america"])

    def test_a_short_head_word_is_not_offered_alone(self):
        self.assertNotIn("big", c.slug_candidates("Big Data Co"))

    def test_punctuation_and_apostrophes(self):
        self.assertEqual(c.slug_candidates("Alarm.com"), ["alarmcom"])
        self.assertEqual(c.slug_candidates("O'Reilly"), ["oreilly"])

    def test_fields_that_are_not_company_names_are_left_alone(self):
        # HN's "company" is the first line of a post; Built In sometimes
        # carries a slash-joined pair. Mangling those into slugs is noise.
        for junk in ["Sudowrite | https://sudowrite.com | REMOTE",
                     "Life Fitness / Hammer Strength",
                     "Hudson Information Technology and Manpower Services",
                     "", None, "  "]:
            with self.subTest(junk=junk):
                self.assertEqual(c.slug_candidates(junk), [])


class TestBoardList(unittest.TestCase):

    def test_builtins_alone_when_nothing_discovered(self):
        self.assertEqual(c.board_list("greenhouse", ["stripe"], make_ctx()),
                         ["stripe"])

    def test_discovered_slugs_are_appended(self):
        ctx = make_ctx(boards_found={"greenhouse": ["klaviyo"],
                                     "lever": ["gopuff"]})
        self.assertEqual(c.board_list("greenhouse", ["stripe"], ctx),
                         ["stripe", "klaviyo"])

    def test_a_rediscovered_builtin_is_not_listed_twice(self):
        ctx = make_ctx(boards_found={"greenhouse": ["stripe"]})
        self.assertEqual(c.board_list("greenhouse", ["stripe"], ctx), ["stripe"])

    def test_known_slugs_covers_builtins_and_finds(self):
        known = c.known_slugs({"found": {"lever": ["wealthfront"]}})
        self.assertIn("wealthfront", known)
        self.assertIn("stripe", known)      # a built-in Greenhouse board


class TestDiscoverBoards(unittest.TestCase):
    """The probe itself is network; everything around it is not."""

    def setUp(self):
        self.real = c.probe_board
        self.probed = []

    def tearDown(self):
        c.probe_board = self.real

    def stub(self, hosts):
        def probe(slug, ctx):
            self.probed.append(slug)
            return hosts.get(slug)
        c.probe_board = probe

    def run_discovery(self, names, boards, today="2026-08-31"):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            hits = c.discover_boards(names, boards, today, make_ctx())
        return hits, buf.getvalue()

    def test_a_hit_is_recorded_under_its_ats(self):
        self.stub({"wealthfront": "lever"})
        boards = {}
        hits, _ = self.run_discovery(["Wealthfront"], boards)
        self.assertEqual(hits, 1)
        self.assertEqual(boards["found"]["lever"], ["wealthfront"])

    def test_a_miss_is_remembered_with_the_date(self):
        self.stub({})
        boards = {}
        self.run_discovery(["Nowhere"], boards)
        self.assertEqual(boards["missed"]["nowhere"], "2026-08-31")

    def test_slugs_already_in_a_list_are_never_probed(self):
        # "stripe" is a built-in Greenhouse board; "gopuff" a built-in Lever one.
        self.stub({})
        self.run_discovery(["Stripe", "Gopuff"], {})
        self.assertEqual(self.probed, [])

    def test_a_fresh_miss_is_not_re_probed(self):
        self.stub({})
        boards = {"missed": {"nowhere": "2026-08-20"}}
        self.run_discovery(["Nowhere"], boards)
        self.assertEqual(self.probed, [])

    def test_a_stale_miss_is_probed_again(self):
        # A company between postings looks exactly like a typo, so a miss
        # has to expire or it is permanent.
        self.stub({"nowhere": "ashby"})
        boards = {"missed": {"nowhere": "2026-01-01"}}
        hits, _ = self.run_discovery(["Nowhere"], boards)
        self.assertEqual(hits, 1)
        self.assertNotIn("nowhere", boards["missed"])

    def test_the_same_company_twice_is_probed_once(self):
        self.stub({})
        self.run_discovery(["Nowhere", "Nowhere", "Nowhere Inc."], {})
        self.assertEqual(self.probed, ["nowhere"])

    def test_the_queue_is_capped_and_the_rest_wait(self):
        self.stub({})
        names = [f"Company{n}" for n in range(c.DISCOVER_CAP + 25)]
        _, out = self.run_discovery(names, {})
        self.assertEqual(len(self.probed), c.DISCOVER_CAP)
        self.assertIn("25 more next run", out)

    def test_nothing_to_do_says_so(self):
        self.stub({})
        hits, out = self.run_discovery([], {})
        self.assertEqual(hits, 0)
        self.assertIn("no new company names", out)

    def test_junk_company_fields_never_reach_the_network(self):
        self.stub({})
        self.run_discovery(["Acme | https://acme.com | hiring"], {})
        self.assertEqual(self.probed, [])


# ==========================================================================
# Wiring invariants — the checks that catch a half-added source
# ==========================================================================
class TestWiring(unittest.TestCase):

    def test_every_source_has_a_dedupe_rank(self):
        # Without a rank a new source defaults to 50 and quietly loses every
        # head-to-head against an aggregator.
        missing = set(c.SOURCES) - set(c.SOURCE_RANK) - set(c.BLOCKED)
        self.assertEqual(missing, set())

    def test_default_sources_all_exist(self):
        self.assertEqual(set(c.DEFAULT_SOURCES) - set(c.SOURCES), set())

    def test_ats_boards_outrank_aggregators(self):
        for ats in ("greenhouse", "ashby", "lever", "workable", "smartrecruiters"):
            for agg in ("linkedin", "adzuna", "jooble"):
                self.assertLess(c.SOURCE_RANK[ats], c.SOURCE_RANK[agg],
                                f"{ats} should outrank {agg}")

    def test_every_written_column_exists_on_a_record(self):
        self.assertEqual(set(c.COLUMNS) - set(c.RECORD_FIELDS), set())

    def test_a_record_writes_no_field_the_columns_do_not_name(self):
        # The other direction, which nothing checked before: a field added to
        # Posting and forgotten in COLUMNS is silently dropped from every CSV
        # the tool writes.
        #
        # "remote" is the one deliberate omission. Every posting that reaches
        # an output file has passed the remote gate, so the column would read
        # True on every row and say nothing.
        self.assertEqual(set(c.RECORD_FIELDS) - set(c.COLUMNS), {"remote"})

    def test_board_lists_have_no_duplicates(self):
        for name in ("GREENHOUSE_BOARDS", "ASHBY_BOARDS", "LEVER_BOARDS",
                     "WORKABLE_BOARDS", "SMARTRECRUITERS_BOARDS"):
            boards = getattr(c, name)
            with self.subTest(list=name):
                self.assertEqual(len(boards), len(set(boards)))

    def test_blocked_sources_explain_themselves_and_return_nothing(self):
        for name in c.BLOCKED:
            with self.subTest(source=name):
                self.assertEqual(
                    c.SOURCES[name](make_cfg(), make_ctx()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
