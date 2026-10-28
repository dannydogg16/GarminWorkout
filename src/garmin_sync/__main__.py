import argparse
import json
import re
import sys
from datetime import date, timedelta
from .garmin import Garmin, recorded_effort
from .http import JsonHttp, SyncError
from .planner import normalize, plan, validate_prescriptions, existing_id
from .sheets import Sheets


def run():
    parser = argparse.ArgumentParser(description="Sync running activities. Default: dry-run, no writes.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--latest", action="store_true", help="Select only the most recent run (even if already imported).")
    parser.add_argument("--format-times", action="store_true", help="Migrate duration/pace display to m:ss; requires --apply to write.")
    parser.add_argument("--inspect-effort", action="store_true", help="Read effort-field availability without logging ratings or writing cells.")
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--until", type=date.fromisoformat, default=date.today())
    parser.add_argument("--prescriptions", default="prescriptions.json")
    args = parser.parse_args()
    if args.format_times:
        Sheets(JsonHttp(), write=args.apply).format_times()
        return
    since = args.since or args.until - timedelta(days=30)
    if since > args.until:
        raise SyncError("--since must not be after --until.")
    # Validate local configuration before contacting either service.
    try:
        with open(args.prescriptions, encoding="utf-8") as f:
            prescriptions = validate_prescriptions(json.load(f))
    except (OSError, ValueError):
        raise SyncError("Cannot read prescriptions JSON; check file syntax and path.") from None
    http = JsonHttp()
    garmin = Garmin(http)
    raw = garmin.activities(since.isoformat(), args.until.isoformat())
    activities = sorted([normalize(a) for a in raw], key=lambda a: (a["local"], int(a["id"])), reverse=True)
    if args.latest:
        activities = activities[:1]
    if not activities:
        print("No running activities found in the selected local-date window; no writes performed.")
        return
    if args.inspect_effort:
        summaries = [garmin.summary(a["id"]) for a in activities]
        for field in ("directWorkoutRpe", "perceivedWorkoutEffort"):
            values = [s.get(field) for s in summaries if s.get(field) is not None]
            native = sum(isinstance(v, (int, float)) and not isinstance(v, bool) and 1 <= v <= 10 for v in values)
            scaled = sum(isinstance(v, (int, float)) and not isinstance(v, bool) and 10 < v <= 100 for v in values)
            print(f"Effort inspection: {field}; present {len(values)}; in 1-10 range {native}; in 11-100 range {scaled}.")
        print("Effort inspection complete; no ratings logged and no spreadsheet writes.")
        return
    sheets = Sheets(http, write=args.apply)
    before = sheets.read()
    existing = {existing_id(row[1]): row for row in before[1:] if len(row) > 1 and row[1] not in (None, "")}
    for a in activities:
        row = existing.get(a["id"])
        if row is None or len(row) <= 6 or row[6] in (None, ""):
            a["effort"] = recorded_effort(garmin.summary(a["id"]))
            if a["effort"] is not None:
                a["extra"]["effort_source"] = "Garmin directWorkoutRpe / 10"
    names = {}
    # Only spend requests on workouts if configured matching requires their names.
    for a in activities:
        if any(p["date"] == a["date"] and p.get("garmin_workout_name") and not p.get("activity_id") for p in prescriptions):
            names[a["id"]] = [w.get("workoutName") for w in garmin.workouts(a["id"])]
    changes, imported, matched = plan(before, activities, prescriptions, names)
    print(f"Selected {len(activities)} activities; new {len(imported)}; matched {matched}; "
          f"already present {len(activities) - len(imported)}.")
    effort_updates = sum(bool(re.search(r"!G[0-9]+$", change["range"])) for change in changes)
    print(f"Recorded effort updates: {effort_updates}; existing manual ratings preserved.")
    if args.apply:
        sheets.apply(before, changes)
        after = sheets.read()
        for change in changes:
            row = int(re.search(r"[A-Z](\d+)", change["range"])[1])
            column = re.search(r"!([A-Z]+)", change["range"])[1]
            start = 0
            for character in column:
                start = start * 26 + ord(character) - 64
            start -= 1
            actual = after[row - 1][start:start + len(change["values"][0])]
            actual += [""] * (len(change["values"][0]) - len(actual))
            if actual != change["values"][0]:
                raise SyncError("Post-write verification failed; inspect the Run Log before rerunning.")
        # Verify formulas and untouched columns/tabs are protected by the write ranges;
        # tests assert exact ranges. Concurrent manual edits cannot be made atomic by Sheets.
        print("Live write verified." if changes else "No writes needed.")
    else:
        print("Dry run complete: no spreadsheet writes. Use --apply only after reviewing the mapping.")


if __name__ == "__main__":
    try:
        run()
    except SyncError as error:
        print(f"Sync stopped: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        # No traceback/response payload: dependency errors can include auth material.
        print("Sync stopped: unexpected error; check configuration and run the offline tests.", file=sys.stderr)
        sys.exit(1)
