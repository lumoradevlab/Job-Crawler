"""Writing the run out: the CSV/JSON/links trio and the rejection report."""

import csv
import json

from ..filters.rules import _SKIPPED


COLUMNS = ["source", "title", "company", "location", "us", "posted",
           "first_seen", "easy_apply", "salary_min", "salary_max",
           "salary_currency", "salary_predicted", "url", "apply_url",
           "query", "description"]


SALARY_FIELDS = ("salary_min", "salary_max", "salary_currency",
                 "salary_predicted")

REJECTED_COLUMNS = ["source", "reason", "title", "company", "location",
                    "posted", "url"]


def report_rejections(rejected, base):
    """Write every dropped posting next to the rule that dropped it.

    The gate is otherwise silent, and silence is ambiguous: a run that
    returns 12 jobs out of 900 looks the same whether it was a quiet week or
    a regex that has stopped matching. This is also the cheap way to audit
    the remote call, which is the crawl's weakest link — sort the file by
    reason, read the "onsite" and "not-remote" rows, and the false-negative
    rate is right there instead of being sampled by hand.
    """
    rows = [{"source": src, "reason": "not-mobile: dropped by the source's "
                                      "own title gate, before the main one",
             "title": title, "company": "", "location": "",
             "posted": "", "url": ""}
            for src, title in _SKIPPED]
    rows += [dict({k: str(job.get(k, "") or "") for k in REJECTED_COLUMNS},
                  reason=why)
             for job, why in rejected]

    path = base + "_rejected.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REJECTED_COLUMNS,
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    by_rule = {}
    for r in rows:
        rule = r["reason"].split(":", 1)[0]
        by_rule.setdefault(rule, {})
        by_rule[rule][r["source"]] = by_rule[rule].get(r["source"], 0) + 1

    print(f"\n[why] {len(rows)} postings rejected "
          f"({len(_SKIPPED)} of them by a source's own title gate)")
    for rule, sources in sorted(by_rule.items(),
                                key=lambda kv: -sum(kv[1].values())):
        top = ", ".join(f"{s} {n}" for s, n in
                        sorted(sources.items(), key=lambda kv: -kv[1])[:4])
        print(f"  {rule:<13}{sum(sources.values()):>6}   {top}")
    print(f"  -> {path}")


BOOKKEEPING = ("match_text", "gh_token", "gh_id", "sr_token", "sr_id")


def strip_bookkeeping(jobs):
    """Drop the per-source scratch fields; they are not worth writing out."""
    for j in jobs:
        for k in BOOKKEEPING:
            j.pop(k, None)
    return jobs


def write_outputs(jobs, base):
    strip_bookkeeping(jobs)

    with open(base + ".csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(jobs)
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(jobs, fh, indent=2, ensure_ascii=False)
    with open(base + "_links.txt", "w", encoding="utf-8") as fh:
        fh.writelines(j["url"] + "\n" for j in jobs)
