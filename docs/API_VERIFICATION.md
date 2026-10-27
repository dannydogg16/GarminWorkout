# Official API verification

Verified by fetching Garmin's official pages on 2026-10-27, before implementation:

- [Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/)
- [Personal API FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/)
- [Personal API Swagger](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/)

The Swagger page embeds an OpenAPI 3.0.1 specification (API version 1.0.0) in its JavaScript bundle. Relevant endpoints and schemas were read directly from that specification, rather than inferred from unofficial Garmin libraries.

## Authentication and restrictions

This is the official personal-use API for one user's own data. Commercial applications serving multiple users use the separate developer program. The FAQ describes self-generated keys; it does not state a separate business-application approval requirement for personal use. Creating a key requires logging into Garmin Connect, accepting Garmin's terms, and choosing an expiration of 30, 90, 180 days, or one year. Keys are displayed once. Garmin sends an expiry reminder about a week before expiration. Deleting a key invalidates its tokens too.

Exchange the key using `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token`, with `Authorization: ApiKey <key>`. Use the resulting OAuth2 token as `Authorization: Bearer <token>`. Tokens last 24 hours or until key expiry, whichever occurs first. No refresh tokens are supported. The sync obtains a token each invocation and renews it once following an activity-request 401.

The public token guide does not document the JSON response schema. The implementation accepts `access_token` or `accessToken` and fails closed otherwise. This adapter still needs validation with Danny's actual key; no successful authenticated request is claimed. No password login, browser-cookie reuse, or unofficial scraping is used.

The OpenAPI servers are `https://personal.connectapi.garmin.com` and the China-region `.cn` equivalent. This integration fixes the global `.com` server; a China-region account requires a separate reviewed configuration change. Credentials are never sent to an arbitrary configurable host.

## Supported endpoints used

| Endpoint | Purpose |
| --- | --- |
| `GET /activitylist-service/activities/search/activities` | Paginated activity summaries, filtered by parent `activityType=running`, inclusive `startDate` and `endDate` based on activity-local time; `start` offset and `limit` (99 default). |
| `GET /activity-service/activity/{activityId}/workouts` | Optional exact workout-name matching; an array of `ActivityWorkoutDTO`, empty when there are none. Only called when prescriptions need it. |

Also verified in the specification, but not required/called: `GET /activity-service/activity/{activityId}` (nested detailed summary) and `GET /download-service/files/activity/{activityId}` (original file). The personal API exposes other health endpoints; this integration does not access them.

`SummarizedActivityDTO` documents activity ID, local/GMT start, type, distance (meters), duration (seconds), average/max HR (bpm), average speed (m/s), elevation gain (meters), calories (kcal), running cadence (steps/min), power (watts), and training effects. Missing optional metrics stay blank/absent. Pace in the workbook is duration/distance; Garmin's speed-derived pace is recorded separately when available. No coordinates, route files, owner profile, or activity descriptions are stored.

Neither the fetched FAQ nor relevant Swagger operations specify a numeric request quota. No quota is invented. Requests are sequential, with bounded retries for 429 and selected 5xx/network errors, honoring `Retry-After` and stopping for cooldowns longer than 60 seconds. Other 4xx errors fail with sanitized messages. The default overlapping window is 30 days, with all pages searched, and IDs deduplicated; older late uploads require a manual backfill. Individual malformed required fields stop the batch before writing, rather than silently losing an activity.

## Google authentication references

- [Google Sheets scopes](https://developers.google.com/workspace/sheets/api/scopes)
- [Workload Identity Federation for deployment pipelines](https://cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)
- [Google GitHub authentication action](https://github.com/google-github-actions/auth)

Use short-lived GitHub OIDC credentials to impersonate a dedicated service account. Request only `spreadsheets.readonly` for dry runs or `spreadsheets` for writes; no Drive scope. Google does not provide a Sheets scope limited to particular cells or a single spreadsheet. Restrict file access by sharing only the target spreadsheet with this service account, giving it no broad Drive/domain or project roles. IAM permissions for impersonation are separate from spreadsheet sharing.
