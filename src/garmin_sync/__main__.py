import argparse
import json
import re
import sys
from datetime import date, timedelta
from .garmin import Garmin
from .http import JsonHttp, SyncError
from .planner import normalize, plan, validate_prescriptions
from .sheets import Sheets


def run():
    parser = argparse.ArgumentParser(description="Sync running activities. Default: dry-run, no writes.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--latest", action="store_true", help="Select only the most recent run (even if already imported).")
    parser.add_argument("--format-times", action="store_true", help="Migrate duration/pace display to m:ss; requires --apply to write.")
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
    sheets = Sheets(http, write=args.apply)
    before = sheets.read()
    names = {}
    # Only spend requests on workouts if configured matching requires their names.
    for a in activities:
        if any(p["date"] == a["date"] and p.get("garmin_workout_name") and not p.get("activity_id") for p in prescriptions):
            names[a["id"]] = [w.get("workoutName") for w in garmin.workouts(a["id"])]
    changes, imported, matched = plan(before, activities, prescriptions, names)
    print(f"Selected {len(activities)} activities; new {len(imported)}; matched {matched}; "
          f"already present {len(activities) - len(imported)}.")
    if args.apply:
        sheets.apply(before, changes)
        after = sheets.read()
        for change in changes:
            row = int(re.search(r"[A-Z](\d+)", change["range"])[1])
            start = 0 if "!A" in change["range"] else 7
            actual = after[row - 1][start:start + len(change["values"][0])]
            actual += [""] * (len(change["values"][0]) - len(actual))
            if actual != change["values"][0]:
                raise SyncError("Post-write verification failed; inspect the Run Log before rerunning.")
        # Verify formulas and untouched columns/tabs are protected by the write ranges;
        # tests assert exact ranges. Concurrent manual edits cannot be made atomic by Sheets.
        print("Live write verified." if imported else "No writes needed.")
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
