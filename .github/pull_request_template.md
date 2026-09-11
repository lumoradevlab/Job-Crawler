<!--
What changed, and why. The why is the half that is hard to recover later:
the diff already says what moved.
-->

## What this changes

## Why

<!--
If this fixes a bug, what made it possible — a missing check, a wrong
assumption, a case nobody had hit. That is what stops it recurring.
-->

## Testing

<!--
Which suites, and what a new test would fail against without this change.
A test that passes against the broken version is not testing the fix.

    python3 test_crawler.py test_net.py test_pipeline.py
    python3 test_roles.py test_countries.py test_notify.py test_taps.py
-->

- [ ] The full suite passes
- [ ] New behaviour has a test, and it fails without the change
- [ ] No credentials, tokens or personal data in the diff
