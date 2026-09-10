#!/usr/bin/env python3
"""Tests for the role profiles: what each gate claims, and what it refuses.

The crawler was built for one role, and widening it is the change most able
to break the thing it started as. So the first thing asserted here is that
the Android gate still behaves exactly as it did.

    python3 test_roles.py           # all of it
    python3 test_roles.py -v        # naming each case

Stdlib only, like the crawler itself.
"""

import unittest

import jobcrawler as c
from jobcrawler.roles import DEFAULT_ROLE, PROFILES, combine, resolve


def gate(role, title):
    """Does this profile's title gate accept the title?"""
    p = combine(role if isinstance(role, list) else [role])
    return bool(p.pattern().search(title) and p.role_pattern().search(title))


def rejects(role, title, **over):
    """The rule that drops this posting under this profile, or None."""
    p = combine(role if isinstance(role, list) else [role])
    filters = c.FilterConfig(subject=p.pattern(), role=p.role_pattern(),
                             **over)
    job = c.row("greenhouse", title, "Acme", "Remote - US",
                "https://x.example/1", "2026-09-01")
    return c.rejection(job, filters)


# ==========================================================================
# The gate that was here first must not move
# ==========================================================================
class TestAndroidUnchanged(unittest.TestCase):

    def test_android_is_still_the_default(self):
        self.assertEqual(DEFAULT_ROLE, "android")

    def test_the_titles_it_kept_it_still_keeps(self):
        for t in ["Android Engineer", "Senior Kotlin Developer",
                  "Mobile Software Engineer", "React Native Developer",
                  "iOS/Android Engineer", "Flutter Developer",
                  "Jetpack Compose Developer"]:
            self.assertTrue(gate("android", t), t)

    def test_the_titles_it_dropped_it_still_drops(self):
        for t in ["Backend Engineer", "Product Manager, Mobile",
                  "Android", "Mobile Marketing Lead", "Data Scientist"]:
            self.assertFalse(gate("android", t), t)

    def test_a_run_with_no_role_gates_on_android(self):
        # FilterConfig with no subject falls back to the module pattern.
        job = c.row("greenhouse", "Backend Engineer", "Acme", "Remote - US",
                    "https://x.example/1", "2026-09-01")
        self.assertIsNotNone(c.rejection(job, c.FilterConfig()))


# ==========================================================================
# What each profile claims
# ==========================================================================
class TestProfiles(unittest.TestCase):

    CASES = [
        ("backend", "Senior Backend Engineer", True),
        ("backend", "Python Developer", True),
        ("backend", "Android Engineer", False),
        ("frontend", "React Developer", True),
        ("frontend", "Frontend Engineer", True),
        ("frontend", "Data Scientist", False),
        ("devops", "Site Reliability Engineer", True),
        ("devops", "Kubernetes Platform Engineer", True),
        ("data", "Machine Learning Engineer", True),
        ("data", "Data Analyst", True),
        ("qa", "QA Automation Engineer", True),
        ("qa", "SDET", True),
        ("security", "Application Security Engineer", True),
        ("embedded", "Firmware Engineer", True),
        ("gamedev", "Unity Developer", True),
        ("itsupport", "Systems Administrator", True),
        ("ios", "Swift Developer", True),
        ("ios", "Kotlin Developer", False),
    ]

    def test_each_profile_claims_its_own_titles(self):
        for role, title, want in self.CASES:
            with self.subTest(role=role, title=title):
                self.assertEqual(gate(role, title), want)

    def test_every_profile_has_queries_and_a_summary(self):
        for name, p in PROFILES.items():
            self.assertTrue(p.queries, name)
            self.assertTrue(p.summary, name)

    def test_every_profiles_own_queries_pass_its_own_gate(self):
        # A profile whose searches ask for titles its gate then rejects
        # returns nothing and looks like a broken crawler.
        for name, p in PROFILES.items():
            passing = [q for q in p.queries
                       if p.pattern().search(q) and p.role_pattern().search(q)]
            self.assertTrue(passing, f"{name}: no query passes its own gate")

    def test_an_unknown_role_says_what_does_exist(self):
        with self.assertRaises(KeyError) as caught:
            resolve("astronaut")
        self.assertIn("backend", str(caught.exception))


# ==========================================================================
# Leadership words: the widening that must not widen engineering
# ==========================================================================
class TestLeadershipWords(unittest.TestCase):

    def test_engineering_profiles_refuse_manager_titles(self):
        # "Product Manager, Mobile" matches the mobile subject; it is not a
        # mobile engineering job, and this is the case that caught it.
        for role in ("android", "backend", "devops", "data"):
            self.assertFalse(gate(role, "Product Manager, Mobile"), role)

    def test_product_and_design_accept_them(self):
        self.assertTrue(gate("product", "Technical Product Manager"))
        self.assertTrue(gate("design", "Senior Product Designer"))

    def test_combining_carries_the_extra_words_across(self):
        self.assertTrue(gate(["backend", "product"], "Product Manager"))
        self.assertTrue(gate(["backend", "product"], "Backend Engineer"))


# ==========================================================================
# The non-technical gate
# ==========================================================================
class TestNotTechnical(unittest.TestCase):

    def test_sales_and_recruiting_titles_are_refused(self):
        # These match a discipline regex and are jobs at software companies
        # rather than software jobs — the biggest noise source once the
        # gate widens past one discipline.
        for t in ["Sales Engineer", "Technical Recruiter",
                  "Enterprise Account Executive, Cloud",
                  "Marketing Data Analyst"]:
            self.assertIsNotNone(rejects("all", t), t)

    def test_it_names_the_rule_that_fired(self):
        why = rejects("all", "Sales Engineer")
        self.assertTrue(why.startswith("not-technical"), why)

    def test_real_engineering_titles_still_pass(self):
        for t in ["Software Engineer", "DevOps Engineer", "Data Engineer",
                  "Security Engineer"]:
            self.assertIsNone(rejects("all", t), t)


# ==========================================================================
# Combining
# ==========================================================================
class TestCombine(unittest.TestCase):

    def test_two_profiles_accept_either_discipline(self):
        self.assertTrue(gate(["backend", "devops"], "Backend Engineer"))
        self.assertTrue(gate(["backend", "devops"], "SRE"))

    def test_queries_are_concatenated_without_duplicates(self):
        both = combine(["backend", "devops"])
        self.assertEqual(len(both.queries), len(set(both.queries)))

    def test_one_name_returns_that_profile_unchanged(self):
        self.assertIs(combine(["backend"]), PROFILES["backend"])

    def test_no_names_falls_back_to_the_default(self):
        self.assertEqual(combine([]).name, DEFAULT_ROLE)

    def test_software_covers_the_engineering_disciplines(self):
        for t in ["Backend Engineer", "Frontend Developer", "DevOps Engineer",
                  "Data Engineer", "QA Engineer", "Android Developer"]:
            self.assertTrue(gate("software", t), t)

    def test_all_adds_the_adjacent_technical_roles(self):
        for t in ["IT Support Specialist", "Technical Product Manager",
                  "UX Designer"]:
            self.assertTrue(gate("all", t), t)


if __name__ == "__main__":
    unittest.main(verbosity=1)
