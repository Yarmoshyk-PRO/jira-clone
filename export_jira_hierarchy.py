#!/usr/bin/env python3
"""
Export epics, tasks and subtasks from a Jira project into a CSV file with the hierarchical
structure Epic → Task → Subtask.

Usage:
    python export_jira_hierarchy.py --project-key PROJ --output output.csv

Environment variables required:
    JIRA_URL         Base URL of your Jira instance, e.g. https://your-domain.atlassian.net
    JIRA_USER        Jira username (usually your email address)
    JIRA_API_TOKEN   API token generated from your Jira account (https://id.atlassian.com/manage-profile/security/api-tokens)

Example:
    export JIRA_URL="https://acme.atlassian.net"
    export JIRA_USER="alice@example.com"
    export JIRA_API_TOKEN="<token>"
    python export_jira_hierarchy.py -k ACME -o acme_issues.csv

The resulting CSV will contain the columns:
    epic_key, epic_summary, task_key, task_summary, subtask_key, subtask_summary
Each task appears on its own line; if the task has no subtasks the subtask columns are blank.
"""

import os
import csv
import argparse
from typing import List
from jira import JIRA, Issue


EPIC_ISSUE_TYPE = "Epic"


def fetch_all(jira: JIRA, jql: str, fields: List[str] | None = None) -> List[Issue]:
    """Fetch *all* issues matching the JQL, transparently handling Jira pagination."""
    issues: List[Issue] = []
    batch_size = 100
    start_at = 0
    while True:
        batch = jira.search_issues(jql_str=jql, startAt=start_at, maxResults=batch_size, fields=fields)
        issues.extend(batch)
        if len(batch) < batch_size:
            break
        start_at += batch_size
    return issues


def main():
    parser = argparse.ArgumentParser(description="Export epics, tasks and subtasks from a Jira project to CSV")
    parser.add_argument("--project-key", "-k", required=True, help="Key of the Jira project, e.g. PROJ")
    parser.add_argument("--output", "-o", default="jira_hierarchy.csv", help="Path to output CSV file")
    args = parser.parse_args()

    # --- Authenticate -------------------------------------------------------
    jira_url = os.getenv("JIRA_URL")
    jira_user = os.getenv("JIRA_USER")
    jira_token = os.getenv("JIRA_API_TOKEN")

    if not jira_url or not jira_user or not jira_token:
        raise EnvironmentError("JIRA_URL, JIRA_USER and JIRA_API_TOKEN environment variables must be set.")

    jira = JIRA(server=jira_url, basic_auth=(jira_user, jira_token))

    # --- Collect epics ------------------------------------------------------
    epic_jql = f"project = {args.project_key} AND issuetype = \"{EPIC_ISSUE_TYPE}\" ORDER BY key"
    epics = fetch_all(jira, epic_jql, fields=["summary"])

    print(f"Found {len(epics)} epics in project {args.project_key}.")

    # --- Prepare CSV writer -------------------------------------------------
    with open(args.output, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow([
            "epic_key",
            "epic_summary",
            "task_key",
            "task_summary",
            "subtask_key",
            "subtask_summary",
        ])

        # --- Loop epics -----------------------------------------------------
        for epic in epics:
            epic_key = epic.key
            epic_summary = epic.fields.summary

            # Fetch tasks linked to the epic (Story, Task, Bug, etc.).
            task_jql = f'parent = {epic_key} ORDER BY key'
            tasks = fetch_all(jira, task_jql, fields=["summary", "subtasks"])

            print(f"Found {len(tasks)} tasks in project {epic_key}. by {task_jql}")

            if not tasks:
                # Epic without child tasks ⇒ write a single row with blanks for task/subtask
                writer.writerow([epic_key, epic_summary, "", "", "", ""])
                continue

            for task in tasks:
                task_key = task.key
                task_summary = task.fields.summary or ""

                if not task.fields.subtasks:
                    # Task without subtasks ⇒ single row, blank subtask columns
                    writer.writerow([epic_key, epic_summary, task_key, task_summary, "", ""])
                    continue

                # Task with subtasks ⇒ multiple rows, one per subtask
                for sub in task.fields.subtasks:
                    sub_issue = jira.issue(sub.key, fields=["summary"])
                    writer.writerow([
                        epic_key,
                        epic_summary,
                        task_key,
                        task_summary,
                        sub_issue.key,
                        sub_issue.fields.summary or "",
                    ])

    print(f"Export completed: {args.output}")


if __name__ == "__main__":
    main()
