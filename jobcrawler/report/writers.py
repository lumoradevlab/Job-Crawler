"""Writing the run out: the CSV/JSON/links trio and the rejection report."""

import csv
import json



COLUMNS = ["source", "title", "company", "location", "us", "posted",
           "first_seen", "easy_apply", "salary_min", "salary_max",
           "salary_currency", "salary_predicted", "url", "apply_url",
           "query", "description"]


SALARY_FIELDS = ("salary_min", "salary_max", "salary_currency",
                 "salary_predicted")

REJECTED_COLUMNS = ["source", "reason", "title", "company", "location",
                    "posted", "url"]


def report_rejections(rejected, base, skipped=(), report=None):
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
            for src, title in skipped]
    rows += [dict({k: str(getattr(job, k, "") or "")
                   for k in REJECTED_COLUMNS}, reason=why)
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

    say = report.line if report is not None else print
    say(f"\n[why] {len(rows)} postings rejected "
        f"({len(skipped)} of them by a source's own title gate)")
    for rule, sources in sorted(by_rule.items(),
                                key=lambda kv: -sum(kv[1].values())):
        top = ", ".join(f"{s} {n}" for s, n in
                        sorted(sources.items(), key=lambda kv: -kv[1])[:4])
        say(f"  {rule:<13}{sum(sources.values()):>6}   {top}")
    say(f"  -> {path}")


def write_outputs(jobs, base):
    """Write the run's three files.

    There is no scratch to strip on the way out any more: a Posting declares
    its fields and keeps per-source working state in `ref`, which as_record()
    does not emit. The old BOOKKEEPING tuple had to be kept in step with every
    source by hand, and forgetting it put a stray column in someone's CSV.
    """
    records = [j.as_record() for j in jobs]

    with open(base + ".csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
    with open(base + "_links.txt", "w", encoding="utf-8") as fh:
        fh.writelines(j.url + "\n" for j in jobs)
