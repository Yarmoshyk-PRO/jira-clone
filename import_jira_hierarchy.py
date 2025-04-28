#!/usr/bin/env python3
"""import_jira_hierarchy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Create Epics → Tasks → Sub‑tasks **only if they don’t already exist** in a target Jira
project, then attach the Tasks to their Epics via ``add_issues_to_epic()``.

CSV format (lower‑snake headers)
--------------------------------
The script now expects *lower‑snake‑case* field names so it can travel between tools
(scripts, spreadsheets, dbs) without requiring quoted headers:

    epic_key, epic_summary, task_key, task_summary, subtask_key, subtask_summary

Any Task row that has **no Sub‑tasks** can leave the three sub‑task columns empty.

Why a two‑phase import?
-----------------------
Linking Tasks to Epics *after* creation avoids problems with locked / renamed “Epic Link”
custom fields that often differ between Jira Cloud sites.

How we detect duplicates
------------------------
An issue is considered existing when another issue **in the destination project** has the
same **issue type** **and** an identical **Summary** (case‑insensitive). Swap this for a
label or external‑ID match if you prefer.

Usage
~~~~~
::

    # dry‑run — no Jira writes
    python import_jira_hierarchy.py --csv jira_export.csv \
            --project-key NEWPROJ --dry-run

    # live run — creates only missing issues, then links
    python import_jira_hierarchy.py --csv jira_export.csv --project-key NEWPROJ

Optional flags::

    --epic-name-field  customfield_12345   # if your Epic Name field ≠ default
    --batch-size       50                  # number of Tasks per add_issues_to_epic() batch

Environment vars (same as exporter)::

    JIRA_URL, JIRA_USER, JIRA_API_TOKEN

Dependencies::

    pip install jira python-dotenv

------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

from jira import JIRA, Issue  # type: ignore

# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Import Epic → Task → Sub‑task hierarchy.")
    ap.add_argument("--csv", required=True, help="Path to the CSV file exported earlier")
    ap.add_argument("--project-key", required=True, help="Destination Jira project key")
    ap.add_argument(
        "--dry-run", action="store_true", help="Parse and log but do *not* write to Jira"
    )
    ap.add_argument(
        "--epic-name-field",
        default="customfield_10011",
        help="Field ID for Epic Name (Jira Cloud default is customfield_10011)",
    )
    ap.add_argument(
        "--batch-size", type=int, default=50, help="Batch size for add_issues_to_epic()",
    )
    return ap.parse_args()


# ---------------------------------------------------------------------
# Jira helpers
# ---------------------------------------------------------------------


def jira_client() -> JIRA:
    url = os.getenv("JIRA_URL")
    user = os.getenv("JIRA_USER")
    token = os.getenv("JIRA_API_TOKEN")
    if not (url and user and token):
        sys.exit("JIRA_URL, JIRA_USER, JIRA_API_TOKEN env vars must be set!")
    return JIRA(server=url, basic_auth=(user, token))


def existing_issues_index(jira: JIRA, project_key: str) -> Dict[Tuple[str, str], Issue]:
    """Return {(issuetype, lower(summary)): jira.Issue} for all issues in project."""
    idx: Dict[Tuple[str, str], Issue] = {}
    start_at = 0
    max_results = 100
    jql = f"project = {project_key}"
    while True:
        chunk = jira.search_issues(
            jql,
            startAt=start_at,
            maxResults=max_results,
            fields=["summary", "issuetype"],
            expand=None,
        )
        if not chunk:
            break
        for issue in chunk:
            key = (issue.fields.issuetype.name.lower(), issue.fields.summary.lower())
            idx.setdefault(key, issue)
        start_at += max_results
    return idx


def issue_exists(idx: Dict[Tuple[str, str], Issue], issue_type: str, summary: str) -> Issue | None:
    return idx.get((issue_type.lower(), summary.lower()))


# ---------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------


def load_rows(csv_path: str) -> List[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = [
            "epic_key",
            "epic_summary",
            "task_key",
            "task_summary",
            "subtask_key",
            "subtask_summary",
        ]
        missing = [h for h in required if h not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV missing headers: {', '.join(missing)}")
        return list(reader)


# ---------------------------------------------------------------------
# Main import routine
# ---------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    rows = load_rows(args.csv)
    jira = jira_client()

    print("Indexing existing issues …", file=sys.stderr)
    idx = existing_issues_index(jira, args.project_key)
    print(f"Indexed {len(idx)} existing issues", file=sys.stderr)

    epic_map: Dict[str, Issue] = {}
    task_map: Dict[str, Issue] = {}

    # 1️⃣ Create Epics that don't exist --------------------------------
    for r in rows:
        summary = r["epic_summary"].strip()
        if not summary:
            continue  # defensive
        if summary in epic_map:
            continue  # already processed in this run
        existing = issue_exists(idx, "Epic", summary)
        if existing:
            epic_map[summary] = existing
            continue
        if args.dry_run:
            print(f"DRY‑RUN: would create Epic '{summary}'", file=sys.stderr)
            epic_map[summary] = object()  # type: ignore[assignment]
            continue
        issue = jira.create_issue(
            project={"key": args.project_key},
            issuetype={"name": "Epic"},
            summary=summary,
            fields={args.epic_name_field: summary},
        )
        print(f"Created EPIC {issue.key}: {summary}", file=sys.stderr)
        epic_map[summary] = issue
        idx[("epic", summary.lower())] = issue  # extend index

    # 2️⃣ Create Tasks + Sub‑tasks ------------------------------------
    epic_to_task_summaries: Dict[str, List[str]] = defaultdict(list)

    for r in rows:
        epic_summary = r["epic_summary"].strip()
        task_summary = r["task_summary"].strip()
        sub_summary = r["subtask_summary"].strip()

        # TASK --------------------------------------------------------
        if task_summary and task_summary not in task_map:
            existing_task = issue_exists(idx, "Task", task_summary) or issue_exists(idx, "Story", task_summary)
            if existing_task:
                task_map[task_summary] = existing_task
            else:
                if args.dry_run:
                    print(f"DRY‑RUN: would create Task '{task_summary}'", file=sys.stderr)
                    task_map[task_summary] = object()  # type: ignore[assignment]
                else:
                    issue = jira.create_issue(
                        project={"key": args.project_key},
                        issuetype={"name": "Task"},
                        summary=task_summary,
                    )
                    print(f"Created TASK {issue.key}: {task_summary}", file=sys.stderr)
                    task_map[task_summary] = issue
                    idx[(issue.fields.issuetype.name.lower(), task_summary.lower())] = issue
            # record for linking later
            epic_to_task_summaries[epic_summary].append(task_summary)

        # SUB‑TASK ----------------------------------------------------
        if sub_summary:
            parent_issue = task_map.get(task_summary)
            if parent_issue is None:
                print(
                    f"⚠️  CSV order error: task '{task_summary}' missing before sub‑task",
                    file=sys.stderr,
                )
                continue
            # Check if sub‑task exists under parent
            existing_sub = None
            if isinstance(parent_issue, Issue):
                parent_details = jira.issue(parent_issue.key, fields=["subtasks"])
                for sub in parent_details.fields.subtasks:
                    if sub.fields.summary.lower() == sub_summary.lower():
                        existing_sub = sub
                        break
            if existing_sub:
                continue  # already there
            if args.dry_run:
                print(
                    f"DRY‑RUN: would create Sub‑task '{sub_summary}' under '{task_summary}'",
                    file=sys.stderr,
                )
            else:
                jira.create_issue(
                    project={"key": args.project_key},
                    issuetype={"name": "Sub-task"},
                    summary=sub_summary,
                    parent={"key": parent_issue.key} if isinstance(parent_issue, Issue) else None,
                )
                print(
                    f"Created SUB‑TASK under {parent_issue.key if isinstance(parent_issue, Issue) else task_summary}: {sub_summary}",
                    file=sys.stderr,
                )

    # 3️⃣ Link Tasks to Epics -----------------------------------------
    if args.dry_run:
        print("DRY‑RUN: would now link tasks to epics", file=sys.stderr)
        return

    for epic_summary, task_summaries in epic_to_task_summaries.items():
        epic_issue = epic_map.get(epic_summary)
        if not isinstance(epic_issue, Issue):
            continue  # placeholder in dry‑run or error

        task_keys = [task_map[s].key for s in task_summaries if isinstance(task_map.get(s), Issue)]
        for i in range(0, len(task_keys), args.batch_size):
            batch = task_keys[i : i + args.batch_size]
            if not batch:
                continue
            try:
                jira.add_issues_to_epic(epic_issue.key, batch)
                print(f"Linked {len(batch)} tasks → {epic_issue.key}", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"Failed to link tasks to {epic_issue.key}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
