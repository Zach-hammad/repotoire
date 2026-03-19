# PR Comment Bot Design

## Goal

Post a summary comment on pull requests showing score, grade, and top 5 findings. Updates existing comment on re-runs instead of creating duplicates.

## Architecture

New `scripts/comment.sh` runs as a composite step in the action after `outputs.sh`. On PR events, it reads the JSON results, builds a markdown comment, and creates or updates a comment via `gh api`. Controlled by a `comment` input (default: `true`).

## JSON Finding Schema

From `repotoire-cli/src/models.rs`, the `Finding` struct serializes to:

```json
{
  "severity": "high",
  "title": "SQL injection in query parameter",
  "description": "...",
  "affected_files": ["src/auth.py"],
  "line_start": 42,
  "line_end": 45,
  "detector": "sql-injection",
  "category": "security"
}
```

Key field names for the script: `severity`, `title`, `affected_files[0]`, `line_start`.

## Comment Format

```markdown
<!-- repotoire-comment -->
### Repotoire Analysis

| Score | Grade | Findings | Critical | High |
|-------|-------|----------|----------|------|
| 72.3  | B-    | 14       | 0        | 3    |

<details>
<summary>Top findings (5)</summary>

| Severity | File | Finding |
|----------|------|---------|
| high | [`src/auth.py:42`](https://github.com/owner/repo/blob/abc123/src/auth.py#L42) | SQL injection in query parameter |
| high | [`src/api.py:18`](https://github.com/owner/repo/blob/abc123/src/api.py#L18) | Command injection via user input |
| high | [`src/util.py:91`](https://github.com/owner/repo/blob/abc123/src/util.py#L91) | Hardcoded credential |
| medium | [`src/db.py:55`](https://github.com/owner/repo/blob/abc123/src/db.py#L55) | N+1 query in loop |
| medium | [`src/app.py:12`](https://github.com/owner/repo/blob/abc123/src/app.py#L12) | Broad exception handler |

</details>
```

### File links

Constructed as: `${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/blob/${HEAD_SHA}/${file}#L${line}`

Where `HEAD_SHA` comes from `github.event.pull_request.head.sha`, passed to the script via env var.

### Edge cases

- **Zero findings**: Summary table with zeros, no details section. Add "No findings detected." line.
- **Findings with `|` or backticks in title**: Escaped in jq with `gsub` before building the table.
- **No line number**: Show file path without `#L` anchor.
- **Empty `affected_files`**: `affected_files` defaults to `[]` (serde default on `Vec<PathBuf>`). If empty, show just the finding title with no file column content.
- **HEAD_SHA fallback**: `INPUT_HEAD_SHA` may be empty if the action runs in a non-PR context that somehow reaches the comment step. Fall back to `GITHUB_SHA` if empty: `HEAD_SHA="${INPUT_HEAD_SHA:-$GITHUB_SHA}"`.

## Components

### New input in `action.yml`

```yaml
comment:
  description: 'Post analysis summary as a PR comment (pull_request events only)'
  required: false
  default: 'true'
```

### New script: `scripts/comment.sh`

**Required env vars** (passed from action.yml composite step):

| Var | Source | Purpose |
|-----|--------|---------|
| `INPUT_COMMENT` | `${{ inputs.comment }}` | Enable/disable |
| `GH_TOKEN` | `${{ github.token }}` | API auth for comment CRUD |
| `GITHUB_REPOSITORY` | automatic | `owner/repo` for API calls |
| `GITHUB_EVENT_NAME` | automatic | Check if PR event |
| `GITHUB_EVENT_PATH` | automatic | Read PR number from event payload |
| `INPUT_HEAD_SHA` | `${{ github.event.pull_request.head.sha }}` | File link construction |
| `GITHUB_SERVER_URL` | automatic | Link base URL |

**Logic:**

1. Exit early if `INPUT_COMMENT != "true"` or event is not `pull_request`/`pull_request_target`
2. Read PR number from `$GITHUB_EVENT_PATH` via `jq -r '.pull_request.number'`
3. Read JSON results file, extract score/grade/counts + top 5 findings. Severity sort requires numeric mapping since jq can't sort strings in custom order: `{"critical":0,"high":1,"medium":2,"low":3,"info":4}` — assign via `(.severity) as $s | ({"critical":0,"high":1,"medium":2,"low":3,"info":4}[$s] // 5)`, sort by that, take first 5
4. Build markdown body to a temp file — escape `|` and `` ` `` in finding titles
5. Search for existing comment: `gh api repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments --paginate --jq '.[] | select(.body | contains("<!-- repotoire-comment -->")) | .id'` (use `--paginate` to handle PRs with 30+ comments)
6. If found: `PATCH /repos/${GITHUB_REPOSITORY}/issues/comments/${COMMENT_ID}`
7. If not found: `POST /repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments`
8. If API call fails (403 permission error): warn but don't fail the action (`|| echo "::warning::..."`)

### New composite step in `action.yml`

```yaml
- name: PR Comment
  id: comment
  shell: bash
  run: ${{ github.action_path }}/scripts/comment.sh
  if: always()
  env:
    INPUT_COMMENT: ${{ inputs.comment }}
    GH_TOKEN: ${{ github.token }}
    INPUT_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
```

`GITHUB_REPOSITORY`, `GITHUB_EVENT_NAME`, `GITHUB_EVENT_PATH`, and `GITHUB_SERVER_URL` are automatically available — no need to pass them.

### Permissions

Callers need `permissions: pull-requests: write` in their workflow. If missing, the comment step warns but does not fail the action. The README documents this requirement.

## What doesn't change

- `outputs.sh` — still writes step summary and step outputs
- All existing inputs/outputs
- No new dependencies (uses `gh api` and `jq`, both pre-installed on runners)
