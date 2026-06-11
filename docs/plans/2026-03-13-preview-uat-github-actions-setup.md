# Preview UAT GitHub Actions Setup

## Purpose

This runbook explains how to configure the GitHub Actions workflow `.github/workflows/preview-uat.yml` so the preview UAT suite can run successfully against `https://preview.booking.devonwatkins.com`.

---

## What the Workflow Does

The `Preview UAT` workflow:

1. runs on pushes to the `preview` branch
2. can also be run manually with `workflow_dispatch`
3. waits for the preview app to become healthy
4. logs into the preview admin UI
5. creates short-lived API clients
6. exercises the booking-engine API
7. optionally verifies webhook delivery if webhook capture secrets are configured

---

## Required GitHub Actions Secret

Go to:

- GitHub repo -> `Settings`
- `Secrets and variables`
- `Actions`

Add this repository secret:

- `PREVIEW_ADMIN_PASSWORD`

### Expected Value

- the current admin password for `https://preview.booking.devonwatkins.com`

### If Missing

The workflow fails immediately at the `Validate required secrets` step with:

```text
PREVIEW_ADMIN_PASSWORD is required for preview UAT.
```

---

## Optional GitHub Actions Variable

Add this repository variable if you want the test suite to target a specific public appointment type:

- `PREVIEW_APPOINTMENT_TYPE_SLUG`

### Expected Value

- an active public appointment type slug returned by `GET /api/v1/appointment-types`
- example:

```text
rental-house-4072-creek-run-cir-buford
```

### If Missing

The suite automatically selects the first active non-admin appointment type returned by the preview API.

---

## Optional GitHub Actions Secrets for Webhook UAT

These are only needed if you want the webhook-delivery test to run end to end.

Add repository secrets:

- `PREVIEW_WEBHOOK_RECEIVER_URL`
- `PREVIEW_WEBHOOK_EVENTS_URL`
- `PREVIEW_WEBHOOK_EVENTS_TOKEN`

### Expected Meaning

- `PREVIEW_WEBHOOK_RECEIVER_URL`
  - public URL that preview should POST webhook events to
- `PREVIEW_WEBHOOK_EVENTS_URL`
  - read endpoint that returns recently captured webhook requests
- `PREVIEW_WEBHOOK_EVENTS_TOKEN`
  - bearer token used to read from the webhook events endpoint, if required

### If Missing

- the webhook-specific preview UAT test is skipped
- the admin and API lifecycle preview tests still run

---

## Webhook Capture Contract

`PREVIEW_WEBHOOK_EVENTS_URL` must return JSON in one of these shapes:

- a top-level list
- an object containing one of:
  - `events`
  - `data`
  - `items`

Each captured webhook event must include:

- `headers`

And the payload must be available in one of:

- `payload`
- `body_json`
- `json`
- `body` containing raw JSON text

The preview UAT suite matches webhook events by:

- `payload.data.source_reference`

---

## How to Configure in GitHub

1. Open the repository on GitHub.
2. Click `Settings`.
3. Open `Secrets and variables`.
4. Click `Actions`.
5. Under `Repository secrets`, add:
   - `PREVIEW_ADMIN_PASSWORD`
   - optionally the webhook secrets listed above
6. Under `Repository variables`, add:
   - optionally `PREVIEW_APPOINTMENT_TYPE_SLUG`

---

## How to Rerun the Workflow

After saving the secret(s):

1. Go to the repo `Actions` tab.
2. Open the `Preview UAT` workflow.
3. Either:
   - click `Run workflow`
   - or push a new commit to `preview`

---

## Healthy Outcome

When configured correctly, the workflow should:

1. pass the `Validate required secrets` step
2. wait for `https://preview.booking.devonwatkins.com/health`
3. run `pytest -q tests/uat_preview -rs`
4. pass the admin/API tests
5. either:
   - pass webhook UAT if webhook secrets are configured
   - or skip webhook UAT cleanly if they are not configured

---

## Quick Troubleshooting

### Failure: `PREVIEW_ADMIN_PASSWORD is required for preview UAT.`

Cause:

- repository secret `PREVIEW_ADMIN_PASSWORD` is missing

Fix:

- add the secret in GitHub Actions settings

### Failure: admin login does not succeed

Cause:

- the stored preview admin password does not match the GitHub secret

Fix:

- update `PREVIEW_ADMIN_PASSWORD` to the actual preview admin password

### Failure: no appointment types found

Cause:

- preview has no active public appointment types
- or `PREVIEW_APPOINTMENT_TYPE_SLUG` points at a missing/inactive type

Fix:

- activate at least one public appointment type
- or update/remove `PREVIEW_APPOINTMENT_TYPE_SLUG`

### Webhook test skipped

Cause:

- webhook secrets are not configured

Fix:

- add `PREVIEW_WEBHOOK_RECEIVER_URL`
- add `PREVIEW_WEBHOOK_EVENTS_URL`
- add `PREVIEW_WEBHOOK_EVENTS_TOKEN` if your capture service requires auth

---

## Related Files

- workflow: `.github/workflows/preview-uat.yml`
- tests: `tests/uat_preview/`
- docs: `README.md`
- agent context: `CLAUDE.md`
