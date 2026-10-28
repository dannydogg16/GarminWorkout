# Garmin → Danny Training Tracker

Official Garmin Connect Personal API → your existing **online** [Danny Training Tracker](https://docs.google.com/spreadsheets/d/1YThajlerH-ftK-GfINcCCgi9arqCNLWOycBIvbbFJUA/edit). The Excel export was used only to inspect structure; it is not uploaded, replaced, or committed.

**Live authentication, a one-activity import, and repeat-run deduplication passed on 2026-10-28. Daily imports remain gated by `ENABLE_DAILY_SYNC=true`; review the imported row before enabling them.** See [validation evidence](docs/VALIDATION.md) and [official API verification](docs/API_VERIFICATION.md).

## Spreadsheet mapping

The uploaded workbook has Progress, Program, Strength Log, Run Log, and Profile tabs. Run Log has 300 rows included by the existing reporting formulas, although more rows are formatted. Program contains strength exercises, not dated running prescriptions. Profile contains a tentative run-club preference, not a committed schedule. Those entries are treated as data, not authorization to change any tab.

| Run Log column | Write policy |
| --- | --- |
| A Date | Activity-local calendar date, numeric Sheets date serial; retains existing format |
| B Activity ID | Garmin ID as text; deduplication key |
| C Run type | Garmin running subtype, or explicitly matched prescription's run type |
| D Distance (mi) | Meters ÷ 1609.344 |
| E Duration (m:ss) | Native Sheets duration (timer seconds ÷ 86400), displayed as elapsed minutes:seconds |
| F Avg HR (bpm) | Available average HR, otherwise blank |
| G Effort (1–10) | Recorded Garmin workout RPE, decoded from `directWorkoutRpe` ÷ 10; fills blank cells only, never overwrites a manual rating |
| H Notes / source | JSON containing source, local start, max HR, pace, optional elevation/calories/cadence/power/training effects, match status |
| I Pace (min/mi), J Week starting | Existing formulas retained and checked before a row is used; pace displays as minutes:seconds per mile |

Only unused A:H rows 2–301 are eligible for new activities. No append-to-bottom API is used because prefilled formulas would push entries beyond reporting ranges. Existing IDs anywhere in Run Log are checked. A previously imported activity can receive a recorded effort in a blank G cell; its other cells remain untouched, and manual ratings are never overwritten. Changed headers, duplicate existing IDs, invalid metrics, missing target formulas, or full capacity stop the import before writes. Missing optional metrics are left blank. All writes use RAW, so text cannot execute spreadsheet formulas. Ordinary imports never change formatting, another tab, or existing formulas. Profile's integration-status text is left unchanged.

Effort comes from `GET /activity-service/activity/{activityId}` → `summaryDTO.directWorkoutRpe`. Garmin's official FIT session profile documents workout RPE using a 10× Borg CR10 encoding; the integration divides by 10 and accepts only scores fitting the sheet's 1–10 scale. It does not estimate effort from HR, pace, training load, or training effect, and does not substitute `perceivedWorkoutEffort`, whose API encoding is not documented. Missing, zero, unsupported, or out-of-range values leave G blank. Details are requested only for new runs or imported runs whose G cell is blank. The normal 30-day overlap also picks up effort recorded after the original import; use a manual earlier `since` for older ratings. The `inspect-effort` workflow mode reports field availability without logging the actual ratings or writing cells.

Duration and pace use native numeric time values with `[m]:ss` formatting, so values remain usable in calculations and durations over an hour display total minutes (for example, `72:05`). Progress duration summaries still report decimal **minutes** as labeled: their sum formulas multiply the underlying days by 1440. Numeric pace metrics in JSON notes remain explicitly labeled in min/mi.

For a manual duration entry, type hours:minutes:seconds, for example `0:42:17`; it displays as `42:17`. Do not enter decimal minutes into the new duration column or type `42:17` as an input, which Sheets can interpret as hours:minutes.

The explicit one-time **dry-run-format-times / format-times** workflow modes inspect the live workbook, convert legacy decimal-minute durations, update only the dependent Progress duration formulas and E header, and format Run Log E/I. The applied migration uses one atomic Sheets batch and verifies duration values, calculated Progress totals, unchanged unrelated cells/formulas, and displayed minutes:seconds. It is safe to rerun: the new header and validated formulas identify already-converted units. Ordinary imports never perform this migration or change formulas/formatting. Unknown layout/formulas or text durations stop migration for review.

## 1. Review the code in GitHub

The implementation is on `dannydogg16/GarminWorkout`'s `main` branch. Don't commit the workbook, credentials, `.env` files, or generated authentication files; `.gitignore` covers common paths. Only offline tests run on push/PR. Sync runs only from main, via manual dispatch or a gated schedule. Publishing the code does not configure Google access, install your Garmin secret, or verify a live import.

## 2. Google: keyless access to just the target file

You need a Google Cloud project where you can enable APIs, create a service account, and configure Workload Identity Federation. Organization policies can restrict federation or external spreadsheet sharing; an administrator must resolve those restrictions. No Google service-account JSON key is required or recommended.

In Google Cloud Shell, set these **non-secret** identifiers. Use a project you control. Get GitHub's numeric repository and owner IDs from the GitHub REST API (`GET /repos/dannydogg16/GarminWorkout`) or `gh api repos/dannydogg16/GarminWorkout --jq '{repository_id: .id, owner_id: .owner.id}'`.

```bash
set -euo pipefail
PROJECT_ID='YOUR_GOOGLE_CLOUD_PROJECT_ID'
REPOSITORY_ID='YOUR_NUMERIC_GITHUB_REPOSITORY_ID'
OWNER_ID='YOUR_NUMERIC_GITHUB_OWNER_ID'
POOL_ID='garmin-github'
PROVIDER_ID='github'
SA_NAME='garmin-sheets-sync'

gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com sheets.googleapis.com --project="$PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
gcloud iam service-accounts create "$SA_NAME" --project="$PROJECT_ID" --display-name='Garmin Sheets sync'
gcloud iam workload-identity-pools create "$POOL_ID" --project="$PROJECT_ID" --location=global --display-name='Garmin GitHub'
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --project="$PROJECT_ID" --location=global --workload-identity-pool="$POOL_ID" \
  --issuer-uri='https://token.actions.githubusercontent.com' \
  --attribute-mapping='google.subject=assertion.sub,attribute.repository_id=assertion.repository_id' \
  --attribute-condition="assertion.repository_id == '$REPOSITORY_ID' && assertion.repository_owner_id == '$OWNER_ID' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref == 'dannydogg16/GarminWorkout/.github/workflows/sync.yml@refs/heads/main' && assertion.event_name in ['workflow_dispatch', 'schedule']"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --project="$PROJECT_ID" \
  --role='roles/iam.workloadIdentityUser' \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL_ID/attribute.repository_id/$REPOSITORY_ID"
printf 'GCP_SERVICE_ACCOUNT=%s\n' "$SA_EMAIL"
printf 'GCP_WORKLOAD_IDENTITY_PROVIDER=projects/%s/locations/global/workloadIdentityPools/%s/providers/%s\n' "$PROJECT_NUMBER" "$POOL_ID" "$PROVIDER_ID"
```

These are one-time provisioning commands. If resources already exist, inspect/reuse them rather than recreating or overwriting policies. Attribute conditions bind the trusted numeric repo/owner IDs, main branch, workflow path, and event types. Do not broaden to all GitHub identities to fix an authentication error. Allow a few minutes for IAM propagation. Grant no project-wide editor/owner or Drive access to the service account. No domain-wide delegation is needed.

Open the **online tracker → Share**, enter `GCP_SERVICE_ACCOUNT`'s email, and grant **Editor** access to this single spreadsheet. Keep general access Restricted. Sheets API write access cannot be limited by OAuth to a particular tab; the code enforces cell ranges. If you protect cells, ensure service-account writes to A:F and H in Run Log are allowed, while formula protections can remain.

## 3. GitHub configuration

Repository **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `GARMIN_API_KEY` | Your existing personal API key; enter it directly in GitHub |
| Variable | `GCP_WORKLOAD_IDENTITY_PROVIDER` | Full provider resource printed above |
| Variable | `GCP_SERVICE_ACCOUNT` | Dedicated service-account email |
| Variable | `ENABLE_DAILY_SYNC` | Leave absent or `false` during testing |

The Garmin key is the only persistent application secret; Google uses short-lived federation credentials instead of a stored private key. Tokens remain in process memory, and the Google action cleans up its temporary credential file. No credentials or health-data payloads are printed, committed, uploaded as artifacts, or written into sheet cells. Logs contain counts and sanitized failures. Do not enable shell tracing or HTTP debug logging. Since the repository is public, protect main and review code/workflow changes: anyone able to change trusted workflow code could misuse its credentials. No pull-request code receives sync credentials.

Rotate the Garmin secret before its chosen expiration. Generate a replacement at [Garmin's key settings](https://connect.garmin.com/app/settings/personalApiToken), update the GitHub secret, test, then revoke the old key. No refresh token is needed. A revoked/expired key causes a failed job with no unauthenticated fallback.

## 4. Test exactly one recent run

1. Check that the live Run Log still matches the mapping above. The sync also checks its headers and target I/J formulas. Keep a sheet version-history checkpoint and avoid editing Run Log during the test.
2. In GitHub **Actions → Garmin sync → Run workflow**, select branch **main**, mode **dry-run-latest**. Optionally supply an earlier `since` date if you haven't run in the last 30 days. This authenticates with Garmin and read-only Google scope, validates and plans one latest running activity, and writes nothing. No runs found is not a successful live activity test.
3. A successful dry run should say `Selected 1 activities; new 1` and `Dry run complete`. It deliberately does not put your workout metrics in public Actions logs. Resolve all failures before writing. If it says already present, no duplicate will be created; select a window containing an unimported recent run or wait for a new run.
4. Run mode **apply-latest** to write at most one activity. Inspect its live Run Log row: date, ID, distance in miles, timer duration in minutes, HR, JSON notes, existing pace/week formulas and Progress totals. The job re-reads the written cells and verifies their values. Do not alter main/config or record a newer Garmin activity between the dry run and apply test if you want the same activity.
5. Run **apply-latest** again. It should report already present and no writes needed. This validates live ID deduplication. If a recorded effort became available since the previous run, only its blank G cell may be updated; no new row is created.
6. Only after these checks, set `ENABLE_DAILY_SYNC` to exactly `true`. Mode **sync** is also available for an explicit manual import of all new runs in the chosen window.

The schedule is daily at 11:17 UTC (07:17 New York during daylight time, 06:17 in winter). GitHub can delay scheduled jobs, and scheduled workflows in public repositories may be disabled after 60 days without repository activity. Check Actions periodically; GitHub failure notifications depend on your account settings. Set the variable to `false` to pause daily imports. The scheduled event may still appear with a skipped job while disabled; no credentials are requested then.

Daily runs scan an overlapping 30-day window, not just yesterday, to catch delayed uploads. Garmin IDs in the sheet provide durable state. For older history, manually run sync with `since`; there is no automatic deletion or correction of already-imported activities. Manual entries without Garmin IDs remain untouched; reconcile them manually to avoid counting the same run twice.

## Prescription matching

No dated running prescription exists in the uploaded workbook. `prescriptions.json` therefore starts as `[]`. To enable matching, add explicit prescriptions, for example:

```json
[
  {"id":"run-2026-11-03", "date":"2026-11-03", "run_type":"Tempo", "garmin_workout_name":"Tempo 20"}
]
```

Use activity-local `YYYY-MM-DD`. Match only an exact Garmin workout name on that date, unique to one selected activity and one unused prescription. Alternatively use `"activity_id":"123456789"` instead of the name for an explicit match. Names are fetched from the official activity-workouts endpoint only when needed. Ambiguous/unmatched runs still import with the Garmin subtype and `unmatched_or_ambiguous` in notes; no date-only, pace-based, or strength-session guesses. Existing matches are not reassigned on reruns. Later prescription edits do not retroactively change imported rows.

The repo is public: committed prescriptions are public too. Keep sensitive plans out of it, or use an ignored local JSON file with `--prescriptions prescriptions.local.json` for local/manual use. The supplied GitHub workflow uses the repository's public `prescriptions.json` only.

## Local development and validation

Use the existing checkout in the isolated cloud environment; no additional worktree is needed.

```bash
cd /workspace/GarminWorkout
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.lock
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m garmin_sync --help
```

The lock targets CPython 3.12 on Linux x86_64 and pins transitive dependencies with verified PyPI wheel hashes. Other platforms require a deliberately regenerated lock; do not disable hash or TLS verification. Offline tests exercise deduplication, formula protection, data conversion, matching ambiguity, retry/auth failure paths, and conservative writes with synthetic data. They do not claim real API access. Local live execution additionally needs securely injected `GARMIN_API_KEY` and Google Application Default Credentials; the GitHub WIF setup above authorizes GitHub, not this cloud machine. Don't copy GitHub's generated credential files here or paste keys into shell commands.

## Failure handling and limits

- 401: check Garmin expiry or Google federation configuration; Garmin token renewal is attempted once. 403: check account restrictions, spreadsheet sharing, Sheets API enablement, and protected ranges. Errors omit raw responses and headers.
- 429/selected 5xx/network errors: bounded retries, honoring short cooldowns; long cooldowns stop. Reruns use ID deduplication and fixed ranges, never append retries. If a write succeeds but its response is lost, the same payload can be retried without adding another row.
- A second read detects edits made while planning. Sheets does not offer an atomic compare-and-set on these values. Workflow concurrency serializes its own runs, but a human or another integration could still edit between the final read and write. Avoid simultaneous writers; check version history if a run is interrupted.
- At row 301, stop. Extend Progress summary ranges and Run Log formulas in a separate reviewed spreadsheet change before raising the import capacity. This integration never silently extends or rewrites formulas.
- Live Garmin and Google authentication, layout checks, a one-activity write/readback, and repeat-run deduplication have passed; see the validation evidence. Human review of the imported row and actual scheduled execution remain outstanding. Changes to credentials, account region, or sheet layout can require revalidation.
