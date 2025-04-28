#!/usr/bin/env python3
"""
Import a hierarchy from a CSV into a **new** Jira instance ‑ creating every Epic and Task
first, then associating the Tasks to their Epics with `add_issues_to_epic()` **afterwards**.

Why this extra step?
--------------------
When you migrate data between two Jira Cloud sites the *Epic Link* field is often still
locked or renamed. Creating tasks without the link and then bulk‑attaching them avoids
those headaches while still giving you perfect parity.

CSV columns expected (exact header names):
    epic_key, epic_summary, task_key, task_summary, subtask_key, subtask_summary

If a Task has no Sub‑tasks just leave the sub‑task columns blank.

Usage
-----
```
python import_jira_hierarchy.py \
       --csv jira_export.csv \
       --project-key NEWPROJ \
       [--epic-name-field customfield_10011] \
       [--dry-run]
```

Environment variables required:
    JIRA_URL, JIRA_USER, JIRA_API_TOKEN

* `--dry-run` prints the plan without touching Jira.
* `--epic-name-field` lets you override the custom‑field ID that Jira Cloud uses for
  the mandatory *Epic Name* when you create Epics.

The script logs every new key it gets back from Jira so you can double‑check the mapping.
"""

import csv
import os
import argparse
import sys
import itertools
from collections import defaultdict

from jira import JIRA

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def connect() -> JIRA:
    """Return an authenticated jira.JIRA connection using env‑vars."""
    url = os.getenv("JIRA_URL")
    user = os.getenv("JIRA_USER")
    token = os.getenv("JIRA_API_TOKEN")
    if not all([url, user, token]):
        sys.exit("ERROR: Please set JIRA_URL, JIRA_USER and JIRA_API_TOKEN environment variables.")
    return JIRA(url, basic_auth=(user, token))


def read_csv(path: str):
    """Load rows from the export CSV (handles the odd UTF‑8 BOM automatically)."""
    with open(path, newline="", encoding="utf‑8‑sig") as f:
        return list(csv.DictReader(f))


def chunked(iterable, size):
    """Yield successive *size*‑length chunks from *iterable*."""
    it = iter(iterable)
    while True:
        chunk = list(itertools.islice(it, size))
        if not chunk:
            break
        yield chunk

# ---------------------------------------------------------------------------
# Main migration logic
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Import Jira hierarchy from CSV")
    parser.add_argument("--csv", required=True, help="Path to export CSV")
    parser.add_argument("--project-key", required=True, help="Target project key in the new Jira")
    parser.add_argument("--epic-name-field", default="customfield_10011",
                        help="Custom‑field ID for 'Epic Name' (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="Plan without creating anything")
    args = parser.parse_args()

    rows = read_csv(args.csv)
    jira = connect()

    proj_key = args.project_key
    epic_name_field = args.epic_name_field

    # -------------------------------------------------------------------
    # First pass: create all Epics (and remember their new keys)
    # -------------------------------------------------------------------
    epic_map = {}                      # original_epic_key  -> new_key
    epic_summary_by_orig = {}
    for r in rows:
        print(f"{r}")
        if r["epic_key"] not in epic_summary_by_orig:
            epic_summary_by_orig[r["epic_key"]] = r["epic_summary"]

    print(f"\n==> Creating {len(epic_summary_by_orig)} epics …")
    for orig_key, summary in epic_summary_by_orig.items():
        if args.dry_run:
            new_key = f"DRY‑{orig_key}"
            print(f"[DRY‑RUN] Would create Epic '{summary}' (from {orig_key}) → {new_key}")
        else:
            issue = jira.create_issue(fields={
                "project": {"key": proj_key},
                "summary": summary,
                "issuetype": {"name": "Epic"},
                # epic_name_field: summary,
            })
            new_key = issue.key
            print(f"Created Epic {new_key}  ←  {summary}")
        epic_map[orig_key] = new_key

    # -------------------------------------------------------------------
    # Second pass: create all Tasks (still *unlinked* to epics) and store
    # which original Epic they belong to so we can attach later.
    # -------------------------------------------------------------------
    task_map = {}                      # original_task_key  -> new_key
    task_epic_orig = {}               # original_task_key  -> original_epic_key
    task_summary_by_orig = {}
    for r in rows:
        if r["task_key"] not in task_summary_by_orig:
            task_summary_by_orig[r["task_key"]] = r["task_summary"]
            task_epic_orig[r["task_key"]] = r["epic_key"]

    print(f"\n==> Creating {len(task_summary_by_orig)} tasks …")
    for orig_key, summary in task_summary_by_orig.items():
        if args.dry_run:
            new_key = f"DRY‑{orig_key}"
            print(f"[DRY‑RUN] Would create Task '{summary}' (from {orig_key}) → {new_key}")
        else:
            issue = jira.create_issue(fields={
                "project": {"key": proj_key},
                "summary": summary,
                "issuetype": {"name": "Task"},
            })
            new_key = issue.key
            print(f"Created Task {new_key}  ←  {summary}")
        task_map[orig_key] = new_key

    # -------------------------------------------------------------------
    # Third pass: create all Sub‑tasks under their newly‑created parent tasks.
    # -------------------------------------------------------------------
    print("\n==> Creating sub‑tasks …")
    sub_count = 0
    for r in rows:
        sub_summary = (r["subtask_summary"] or "").strip()
        if not sub_summary:
            continue  # This task has no sub‑task on this row.
        parent_new_key = task_map[r["task_key"]]
        if args.dry_run:
            print(f"[DRY‑RUN] Would create Sub‑task '{sub_summary}' under {parent_new_key}")
        else:
            issue = jira.create_issue(fields={
                "project": {"key": proj_key},
                "summary": sub_summary,
                "issuetype": {"name": "Sub‑task"},
                "parent": {"key": parent_new_key},
            })
            print(f"Created Sub‑task {issue.key}  ←  {sub_summary} (parent {parent_new_key})")
        sub_count += 1
    print(f"Sub‑tasks processed: {sub_count}")

    # -------------------------------------------------------------------
    # Final pass: attach each batch of task_keys to its new Epic using
    # jira.add_issues_to_epic(). (API limit = 50 at a time.)
    # -------------------------------------------------------------------
    print("\n==> Linking tasks to epics with add_issues_to_epic() …")
    epics_to_tasks = defaultdict(list)   # new_epic_key → [new_task_key, …]
    for orig_task_key, orig_epic_key in task_epic_orig.items():
        epics_to_tasks[epic_map[orig_epic_key]].append(task_map[orig_task_key])

    for epic_key, task_keys in epics_to_tasks.items():
        if args.dry_run:
            print(f"[DRY‑RUN] Would link {len(task_keys)} tasks to {epic_key}")
        else:
            for chunk in chunked(task_keys, 50):
                jira.add_issues_to_epic(epic_key, chunk)
            print(f"Linked {len(task_keys)} tasks → {epic_key}")

    print("\n✔ Import complete.")


if __name__ == "__main__":
    main()
