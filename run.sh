#!/bin/bash
# Short names for the crawler runs worth doing. Usage: ./run.sh <mode>
set -e
cd "$(dirname "$0")"

ALL="linkedin greenhouse ashby lever builtin arc wwr hn remotive remoteok arbeitnow workingnomads himalayas"

# The new sources and the --verified-remote-only flag only exist on the
# fix-remote-verification branch, so say so rather than failing obscurely.
if ! python3 crawler.py --help 2>&1 | grep -q verified-remote-only; then
  echo "! You are on the old crawler (branch: $(git branch --show-current))."
  echo "  Run:  git switch fix-remote-verification"
  exit 1
fi

case "$1" in
  quick)    # one request, ~5 seconds — proves it runs at all
    python3 crawler.py --source workingnomads -o t_quick ;;

  linkedin) # the change in action: LinkedIn jobs come back "unconfirmed"
    python3 crawler.py --source linkedin -k "Android Developer" -p 1 -o t_linkedin ;;

  strict)   # same, but drop everything no board actually confirmed as remote
    python3 crawler.py --source linkedin -k "Android Developer" -p 1 \
            --verified-remote-only -o t_strict ;;

  all)      # every source that isn't behind a bot wall, ~5-10 min
    python3 crawler.py --source $ALL -o all_sites ;;

  deep)     # same, plus a real sweep of himalayas' ~100k postings
    python3 crawler.py --source $ALL -p 25 -o deep_sites ;;

  clean)
    rm -f t_quick.* t_strict.* t_linkedin.* t_*_seen.json
    echo "test outputs removed" ;;

  *)
    echo "usage: ./run.sh {quick|linkedin|strict|all|deep|clean}"
    exit 1 ;;
esac
