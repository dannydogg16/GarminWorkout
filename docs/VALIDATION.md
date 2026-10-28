# Validation status

## Live validation: 2026-10-28

The user configured the Garmin repository secret and keyless Google access. These actual GitHub Actions runs completed successfully:

- [One-activity dry run](https://github.com/dannydogg16/GarminWorkout/actions/runs/40710694752): authenticated with Garmin and Google, read the live Run Log, validated layout and target formulas, selected one new running activity, and performed no writes.
- [One-activity write test](https://github.com/dannydogg16/GarminWorkout/actions/runs/40710799573): imported one running activity into the existing online spreadsheet and verified written values by reading them back.
- [Repeat import](https://github.com/dannydogg16/GarminWorkout/actions/runs/40710922702): reported one activity already present, zero new activities, and no writes needed.

The first dry run failed with Garmin HTTP 406 before any sheet write. The token request was corrected to use `Accept: */*` and permit a text token response; subsequent runs passed. No credentials, activity IDs, or metrics were logged during diagnosis. All three successful runs also passed the 34 offline tests.

The imported run had no configured prescription match. Formula columns, effort cells, formatting, and other tabs were excluded from write ranges. Human inspection of the imported row and actual daily scheduled execution remain outstanding. Daily synchronization has not been enabled by the assistant; keep `ENABLE_DAILY_SYNC=false` until review. GitHub's Actions secret/variable management API returned HTTP 403 to this session, so stored values cannot be independently inspected or changed here. Successful live authentication confirms the bindings used by the workflow work.

## Offline and workbook validation

- All **43 offline unit/integration tests passed**, including simulated CLI apply-latest and repeat import, formula preservation, missing metrics, duplicate IDs, exact/ambiguous matching, authentication renewal, plain-text token negotiation, bounded retries, minimum Google scopes, dry-run write prevention, concurrent-edit detection, native-duration conversion, preserved minute totals, prevention of double conversion, recorded RPE scaling, blank-effort backfill, and protection of existing ratings.
- The uploaded workbook's Run Log headers and **600 formulas** (I/J, rows 2–301) match the implementation. A synthetic activity planned writes only to A2:F2 and H2. The export was never changed or added to Git.
- All 11 dependency wheel hashes matched PyPI metadata. Hash-checked install completed and was repeated successfully; `pip check` found no broken requirements.
- Workflow YAML parsed, action references were verified as full commit hashes, and embedded shell scripts passed `bash -n`.
- Garmin's official Personal API guide, FAQ, and embedded OpenAPI specification were fetched and inspected. Google Sheets scope and Google federation/action documentation were also fetched.

Live credentials are available to the GitHub workflow, not this cloud machine. Local API execution still needs separate authorized credentials. Publishing source code and saving the cloud environment draft do not publish a new environment snapshot or prove scheduled execution.

## Minute:second display update: 2026-10-28

At the user's request, duration and pace were changed to native numeric time values displayed as `[m]:ss` in Run Log E/I. The E header now says `Duration (m:ss)`. Existing Run Log pace/week formulas were preserved. The total and 16 weekly Progress duration formulas now multiply their day-based sums by 1440, preserving their original numeric-minute results. Other cells/formulas and formatting were left unchanged.

- [Live formatting dry run](https://github.com/dannydogg16/GarminWorkout/actions/runs/40714262571) inspected the full live workbook's formulas and validated one recorded duration without writing.
- [Atomic formatting update](https://github.com/dannydogg16/GarminWorkout/actions/runs/40714420279) succeeded. Readback verified all recorded durations, every affected calculated Progress minute total, unchanged unrelated values/formulas, time formats, and actual formatted minute:second strings. Future imports write native day-based duration values into the formatted range.

The formatting command is explicit and repeatable; ordinary sync never rewrites summary formulas or performs unit migrations. Manual duration entries in the new format must be entered as durations (for example `0:42:17` for 42 minutes, 17 seconds), rather than decimal minutes.

## Recorded effort import: 2026-10-28

The original importer deliberately left subjective effort blank and read only the activity-list summaries. At the user's request, it now retrieves the detail summary's recorded `directWorkoutRpe`, decodes Garmin's 10× RPE representation, and fills G only when blank. No HR/pace inference or training-effect substitution is used. Previously imported rows can receive a late rating without creating another activity or altering their other cells.

- [Read-only field inspection](https://github.com/dannydogg16/GarminWorkout/actions/runs/40715972634) confirmed a recorded direct RPE was available for the recent activity. No rating values were logged.
- [Dry run](https://github.com/dannydogg16/GarminWorkout/actions/runs/40716421292) planned one effort update and zero new activity rows.
- [Live backfill](https://github.com/dannydogg16/GarminWorkout/actions/runs/40716524287) filled one effort cell and verified its value by readback, with zero new activity rows.
- [Repeat run](https://github.com/dannydogg16/GarminWorkout/actions/runs/40716675009) reported zero effort updates, zero new activities, and no writes needed.

Both live runs passed all 43 tests. Manual ratings, formula columns, time formatting, and other existing row content were excluded from the backfill's single G-cell write.
