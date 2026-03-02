# CI/CD — GitHub Actions + Branch Protection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run `pytest` automatically on every push and PR, and require it to pass before any PR can merge to `master`.

**Architecture:** A single GitHub Actions workflow file triggers on push and pull requests. GitHub branch protection rules require the `test` job to pass before a PR can be merged. Coolify continues to auto-deploy from `master` via webhook — nothing about the deploy pipeline changes.

**Tech Stack:** GitHub Actions, Python 3.12, pytest (already in requirements.txt)

---

### Task 1: Create the GitHub Actions workflow

**Files:**
- Create: `.github/workflows/test.yml`

This is YAML config — no unit test applies. Verification is done by pushing a branch and watching GitHub run the check.

**Step 1: Create the workflows directory and file**

```bash
mkdir -p .github/workflows
```

Create `.github/workflows/test.yml` with this exact content:

```yaml
name: Test

on:
  push:
  pull_request:
    branches: [master]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run tests
        run: pytest -v
```

**Why these triggers:**
- `push` (no branch filter): gives instant feedback whenever you push any branch, even before opening a PR
- `pull_request: branches: [master]`: this is the event branch protection checks — it runs when a PR targeting `master` is opened or updated

**Why no `pytest.ini` flags needed:** `pytest.ini` already sets `testpaths = tests`, and e2e tests auto-skip without credentials. The plain `pytest -v` command is correct.

**Step 2: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add GitHub Actions test workflow"
```

**Step 3: Push and verify**

```bash
git push origin master
```

Go to `https://github.com/AlobarQuest/booking-system/actions`. You should see a workflow run appear within 30 seconds. Click it and confirm the `test` job passes (160 passed, 17 skipped is the expected output).

---

### Task 2: Configure branch protection on GitHub

**This task is done entirely in the GitHub UI — no code changes.**

**Step 1: Open branch protection settings**

Go to: `https://github.com/AlobarQuest/booking-system/settings/branches`

Click **"Add branch ruleset"** (or "Add rule" depending on GitHub UI version).

**Step 2: Configure the rule**

- **Branch name pattern:** `master`
- **Require a pull request before merging:** ✅ check this
  - Approvals required: `0` (solo project — you don't need to approve your own PRs)
- **Require status checks to pass before merging:** ✅ check this
  - Click "Add checks" and search for: `test`
  - Select it (it appears after Task 1's workflow has run at least once)
- **Require branches to be up to date before merging:** ✅ check this
- **Do not allow bypassing the above settings:** leave **unchecked** (lets you push directly to `master` for hotfixes without opening a PR)

**Step 3: Save**

Click "Create" or "Save changes".

**Step 4: Verify**

Create a test branch, push it, and open a PR against `master` on GitHub. You should see a "test / test" status check appear on the PR. The merge button should be greyed out until the check passes.

---

### Task 3: Update CLAUDE.md to document the new workflow

**Files:**
- Modify: `CLAUDE.md` — the "Branch workflow" line in the Deployment section

**Step 1: Update the branch workflow description**

Find this line in `CLAUDE.md`:

```
**Branch workflow:** create feature branch/worktree from `master` → implement → merge to `master` → push → reset `preview` to `master` for testing.
```

Replace it with:

```
**Branch workflow:** create feature branch/worktree from `master` → implement → push branch to GitHub → open PR → CI must pass → merge via GitHub UI → Coolify auto-deploys from `master` → reset `preview` to `master` (`git push origin master:preview --force`).
```

**Step 2: Run tests to confirm nothing broken**

```bash
pytest -v
```

Expected: 160 passed, 17 skipped.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update branch workflow to reflect PR-based CI/CD process"
```

**Step 4: Push**

```bash
git push origin master
```

---

## Final verification

Open a new feature branch, make a trivial change, push it, and open a PR against `master`. Confirm:

1. The `test` check appears on the PR within ~60 seconds
2. The check passes (green checkmark)
3. The "Merge pull request" button becomes active
4. After merging, Coolify deploys automatically (check the Coolify dashboard)
