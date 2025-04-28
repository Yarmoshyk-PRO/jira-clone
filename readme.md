# Export/Import Epics, tasks and subtasks from a Jira project into a CSV file with the hierarchical
structure is `Epic` → `Task` → `Subtask`.

Environment variables required:
```bash
    JIRA_URL         Base URL of your Jira instance, e.g. https://your-domain.atlassian.net
    JIRA_USER        Jira username (usually your email address)
    JIRA_API_TOKEN   API token generated from your Jira account (https://id.atlassian.com/manage-profile/security/api-tokens)
```

Usage:
```bash
    python export_jira_hierarchy.py --project-key PROJ --output output.csv
``

## Getting it running
1. Install dependencies

```bash
pip install jira python-dotenv
```

1. Set credentials
```bash
    export JIRA_URL=$(grep jira_url src_config.txt |cut -d "=" -f 2)
    export JIRA_USER=$(grep login src_config.txt |cut -d "=" -f 2)
    export JIRA_API_TOKEN=$(grep token src_config.txt |cut -d "=" -f 2)
```
### Export
```bash
    python3 export_jira_hierarchy.py -k DS2 -o yarmoshyk_ds2.csv
```

The resulting CSV will contain the columns:
* epic_key
* epic_summary
* task_key
* task_summary
* subtask_key
* subtask_summary

Each task appears on its own line; if the task has no subtasks the subtask columns are blank.

### Import
Point it at the new Jira (same env vars as before):

```bash
    export JIRA_URL=$(grep jira_url dst_config.txt |cut -d "=" -f 2)
    export JIRA_USER=$(grep login dst_config.txt |cut -d "=" -f 2)
    export JIRA_API_TOKEN=$(grep token dst_config.txt |cut -d "=" -f 2)
```

Run (dry-run first, then live):
```bash
python import_jira_hierarchy.py --csv jira_export.csv --project-key DSIT --epic-name-field epicname_12345
```