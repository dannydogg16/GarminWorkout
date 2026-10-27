# Validation status

Validated in the current cloud machine on 2026-10-27:

- All **32 offline unit/integration tests passed**, including a simulated CLI apply-latest and repeat import, preservation of formula columns, missing metrics, duplicate IDs, exact/ambiguous matching, authentication renewal, bounded retries, minimum Google scopes, dry-run write prevention, and concurrent-edit detection.
- The actual uploaded workbook's Run Log headers and **600 formulas** (I/J, rows 2–301) match the implementation. A synthetic activity planned writes only to A2:F2 and H2. The export was never changed or added to Git.
- All 11 dependency wheel hashes matched PyPI metadata. Hash-checked install completed and was repeated successfully; `pip check` found no broken requirements.
- Workflow YAML parsed successfully, all action references were verified as full commit hashes, and embedded shell scripts passed `bash -n`.
- Garmin's official Personal API guide, FAQ, and embedded OpenAPI specification were fetched and inspected. Google Sheets scope and Google federation/action documentation were also fetched.
- A live CLI attempt stopped safely because no Garmin key was injected into this machine. Existing machine Google credentials failed authentication; the online sheet's headers/content have not been verified. Attempts to list repository Actions secret/variable names returned HTTP 403; existing GitHub bindings cannot be confirmed from this session.

Not yet validated: actual Garmin token response and activities, Google federation provisioning in the user's project, live Google read/write permissions, real one-activity import/deduplication, and scheduled execution. No online spreadsheet writes have been attempted. See README's staged live test before enabling daily sync. Publishing source code and saving the cloud environment draft do not establish that any of these live operations work.
