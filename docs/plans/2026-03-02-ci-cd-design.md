# CI/CD Design — GitHub Actions + Branch Protection

**Goal:** Run the pytest suite automatically on every PR and require it to pass before merging to `master`. Coolify continues to auto-deploy from `master` via webhook as today.

**Approach:** Option A — GitHub Actions test workflow + GitHub branch protection rules.

---

## Components

### 1. GitHub Actions Workflow

**File:** `.github/workflows/test.yml`

**Triggers:**
- Push to any branch
- Pull request targeting `master`

**Runner:** `ubuntu-latest`, Python 3.12 (matches production)

**Steps:** checkout → setup Python 3.12 → `pip install -r requirements.txt` → `pytest -v`

No secrets required. E2e tests auto-skip when Google credentials are absent.

Job name: `test` (referenced by branch protection).

### 2. Branch Protection Rules

Set once manually in GitHub → Settings → Branches → rule for `master`:

- Require status checks to pass before merging — required check: `test`
- Require branches to be up to date before merging

No push restrictions — direct pushes to `master` still allowed for hotfixes.

### 3. Updated Development Workflow

| Step | Old | New |
|------|-----|-----|
| Finish feature | `git merge feature → git push origin master` | `git push origin <branch> → open PR on GitHub` |
| Gate | Manual (`pytest -v` before push) | Automated (CI must be green) |
| Merge | Local | GitHub UI (merge button) |
| Deploy | Coolify auto-deploys from master | Same |

`preview` branch: reset to master manually after each production deploy, unchanged.

CLAUDE.md branch workflow section updated to reflect PR-based flow.

---

## What Doesn't Change

- Coolify deploy pipeline (webhook → Docker build → container swap)
- `preview` branch workflow
- Test suite itself
- No new secrets or credentials required
