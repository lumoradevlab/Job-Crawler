#!/usr/bin/env python3
"""Tests for --country: the region gate, and what swaps with it.

The crawler graded every posting against one question — will this role take
an applicant based in the US? — and the answer was baked into a module-level
regex. A second country is the change most able to break that, because
Canada sits on the US gate's *reject* list: get the inversion wrong and a
Canadian run drops every job it exists to find.

So the first thing asserted is that the US gate has not moved.

    python3 test_countries.py           # all of it
    python3 test_countries.py -v        # naming each case

Stdlib only, like the crawler itself.
"""

import unittest

import jobcrawler as c
from jobcrawler.filters.countries import (CA, COUNTRIES, DEFAULT_COUNTRY, US,
                                          country_codes, resolve)
from jobcrawler.filters.geo import home_status, us_status


def graded(text, country=None):
    return home_status(text, country)


def rejects(location, country, **over):
    """The rule dropping a posting in this location, or None."""
    filters = c.FilterConfig(country=country, **over)
    job = c.row("greenhouse", "Android Engineer", "Acme", location,
                "https://x.example/1", "2026-09-01")
    return c.rejection(job, filters)


# ==========================================================================
# The gate that was here first must not move
# ==========================================================================
class TestUSUnchanged(unittest.TestCase):

    def test_us_is_still_the_default(self):
        self.assertEqual(DEFAULT_COUNTRY, "us")

    def test_us_status_grades_exactly_as_before(self):
        cases = [("Remote - US", "us"), ("San Francisco, CA", "us"),
                 ("United States", "us"), ("Anywhere", "worldwide"),
                 ("EMEA only", "no"), ("Berlin, Germany", "no"),
                 ("", "unknown"), ("Remote (CET ±3)", "no")]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(us_status(text), want)

    def test_a_filterconfig_with_no_country_grades_for_the_us(self):
        # Every existing caller and test passes no country at all.
        self.assertIsNone(rejects("Remote - US", None))
        self.assertIsNotNone(rejects("Toronto, ON", None))

    def test_the_loose_region_pass_still_runs_last(self):
        # Going first it claims "Yorkshire" for New York and "Mexico City"
        # for New Mexico — the ordering these three tests protect.
        self.assertEqual(us_status("Yorkshire, UK"), "no")
        self.assertEqual(us_status("Mexico City, Mexico"), "no")
        self.assertEqual(us_status("Hampshire, England"), "no")


# ==========================================================================
# Canada, and the inversion
# ==========================================================================
class TestCanada(unittest.TestCase):

    def test_canada_accepts_its_own_postings(self):
        for text in ["Remote - Canada", "Toronto, ON", "Vancouver, BC",
                     "Canada", "Remote (Canada)", "Montreal, QC"]:
            with self.subTest(text=text):
                self.assertEqual(graded(text, CA), "us", text)

    def test_canada_rejects_us_postings(self):
        # The inversion that matters: these are the postings a US run keeps.
        for text in ["Remote - US", "San Francisco, CA", "United States",
                     "Austin, TX", "US only"]:
            with self.subTest(text=text):
                self.assertEqual(graded(text, CA), "no", text)

    def test_the_us_rejects_canadian_postings(self):
        for text in ["Toronto, ON", "Remote - Canada", "Vancouver, BC"]:
            with self.subTest(text=text):
                self.assertEqual(graded(text, US), "no", text)

    def test_both_keep_worldwide_postings(self):
        # A worldwide role takes an applicant from either country.
        for country in (US, CA):
            self.assertEqual(graded("Anywhere", country), "worldwide")
            self.assertEqual(graded("Worldwide", country), "worldwide")

    def test_both_reject_overseas_postings(self):
        for country in (US, CA):
            for text in ["EMEA only", "Berlin, Germany", "Bangalore, India"]:
                with self.subTest(country=country.code, text=text):
                    self.assertEqual(graded(text, country), "no")

    def test_the_ca_abbreviation_is_read_per_country(self):
        # "CA" is California to a US run and an ambiguous token to a
        # Canadian one — the collision that makes a shared list impossible.
        self.assertEqual(graded("San Francisco, CA", US), "us")
        self.assertEqual(graded("San Francisco, CA", CA), "no")


# ==========================================================================
# What the flag moves besides the gate
# ==========================================================================
class TestCountryWiring(unittest.TestCase):

    def test_each_country_names_its_adzuna_index(self):
        self.assertEqual(US.adzuna, "us")
        self.assertEqual(CA.adzuna, "ca")

    def test_each_country_names_a_linkedin_location(self):
        self.assertEqual(US.linkedin, "United States")
        self.assertEqual(CA.linkedin, "Canada")

    def test_an_unknown_country_says_what_does_exist(self):
        with self.assertRaises(ValueError) as caught:
            resolve("zz")
        self.assertIn("ca", str(caught.exception))

    def test_resolve_ignores_case(self):
        self.assertIs(resolve("CA"), CA)

    def test_every_country_is_complete(self):
        for code, country in COUNTRIES.items():
            for field in ("hint", "abbrev", "regions", "foreign",
                          "foreign_abbrev", "adzuna", "linkedin"):
                self.assertTrue(getattr(country, field), f"{code}.{field}")

    def test_the_codes_list_matches_the_table(self):
        self.assertEqual(country_codes(), sorted(COUNTRIES))


# ==========================================================================
# The gate end to end
# ==========================================================================
class TestRejection(unittest.TestCase):

    def test_a_canadian_run_keeps_canadian_jobs(self):
        self.assertIsNone(rejects("Toronto, ON", CA))

    def test_a_canadian_run_drops_us_jobs(self):
        why = rejects("Austin, TX", CA)
        self.assertIsNotNone(why)
        self.assertTrue(why.startswith("region"), why)

    def test_anywhere_turns_the_country_gate_off_entirely(self):
        self.assertIsNone(rejects("Austin, TX", CA, anywhere=True))
        self.assertIsNone(rejects("Toronto, ON", US, anywhere=True))

    def test_strict_drops_worldwide_for_either_country(self):
        for country in (US, CA):
            with self.subTest(country=country.code):
                self.assertIsNotNone(
                    rejects("Anywhere", country, strict_us=True))

    def test_worldwide_is_kept_without_strict(self):
        for country in (US, CA):
            self.assertIsNone(rejects("Anywhere", country))


if __name__ == "__main__":
    unittest.main(verbosity=1)
