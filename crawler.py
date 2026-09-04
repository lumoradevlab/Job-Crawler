#!/usr/bin/env python3
"""Crawl remote Android/mobile developer jobs, US-only by default.

The code lives in the jobcrawler package; this is the entry point for running
it from a clone without installing anything. These are the same program:

    python3 crawler.py --help
    python3 -m jobcrawler --help
    jobcrawler --help              # after pip install .
"""

from jobcrawler.cli import main

if __name__ == "__main__":
    main()
