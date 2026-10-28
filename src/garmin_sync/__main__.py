import argparse
import json
import re
import sys
from datetime import date, timedelta
from .garmin import Garmin, recorded_effort
from .http import JsonHttp, SyncError
from .planner import normalize, plan, validate_prescriptions, existing_id, metric_values, metric_layout, CAPACITY
from .sheets import Sheets


def finish(sheets, before, changes, apply):
    if not apply:
        print("Dry run complete: no spreadsheet writes. Use --apply only after reviewing the mapping.")
        return
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
    print("Live write verified." if changes else "No writes needed.")


def backfill_metrics(http, garmin, apply):
    sheets = Sheets(http, write=apply)
    before = sheets.read()
    if not metric_layout(before):
        raise SyncError("Add the six metric headers in Run Log K:P before backfilling.")
    # Validate existing IDs and layout before spending requests on details.
    plan(before, [], [])
    activities = []
    for row in before[1:CAPACITY]:
        identifier = existing_id(row[1]) if len(row) > 1 else None
        metrics = (list(row[10:16]) + [""] * 6)[:6]
        if identifier and any(value in (None, "") for value in metrics):
            activities.append({"id": identifier, "metrics": metric_values(garmin.summary(identifier), detailed=True)})
    changes, imported, _ = plan(before, activities, [])
    if imported:
        raise SyncError("Backfill must not create activity rows.")
    print(f"Metric backfill: selected {len(activities)} existing activities; blank metric cells to fill {len(changes)}; new activities 0.")
    finish(sheets, before, changes, apply)


def run():
    parser = argparse.ArgumentParser(description="Sync running activities. Default: dry-run, no writes.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--latest", action="store_true", help="Select only the most recent run (even if already imported).")
    parser.add_argument("--format-times", action="store_true", help="Migrate duration/pace display to m:ss; requires --apply to write.")
    parser.add_argument("--inspect-effort", "--inspect-metrics", dest="inspect_effort", action="store_true", help="Read metric availability/consistency without logging values or writing cells.")
    parser.add_argument("--backfill-metrics", action="store_true", help="Fill blank K:P metrics for existing IDs only; --apply writes.")
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
    if args.backfill_metrics:
        backfill_metrics(http, garmin, args.apply)
        return
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
        comparisons = [(a["metrics"][i], metric_values(s, detailed=True)[i])
                       for a, s in zip(activities, summaries) for i in (0, 1)]
        present = [(left, right) for left, right in comparisons if left is not None and right is not None]
        equal = sum(abs(left - right) < 1e-8 for left, right in present)
        doubled = sum(abs(left - 2 * right) < 1e-8 and left != right for left, right in present)
        print(f"Cadence source inspection: compared {len(present)} fields; equal {equal}; list is twice detail {doubled}.")
        print("Effort inspection complete; no ratings logged and no spreadsheet writes.")
        return
    sheets = Sheets(http, write=args.apply)
    before = sheets.read()
    metric_enabled = metric_layout(before)
    existing = {existing_id(row[1]): row for row in before[1:] if len(row) > 1 and row[1] not in (None, "")}
    for a in activities:
        row = existing.get(a["id"])
        needs_effort = row is None or len(row) <= 6 or row[6] in (None, "")
        needs_metrics = metric_enabled and (row is None or len(row) < 16 or any(v in (None, "") for v in row[10:16]))
        if needs_effort or needs_metrics:
            summary = garmin.summary(a["id"])
            if needs_effort:
                a["effort"] = recorded_effort(summary)
                if a["effort"] is not None:
                    a["extra"]["effort_source"] = "Garmin directWorkoutRpe / 10"
            if needs_metrics:
                detail_values = metric_values(summary, detailed=True)
                a["metrics"] = [v if v is not None else fallback for v, fallback in zip(a["metrics"], detail_values)]
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
    metric_updates = sum(bool(re.search(r"![K-P][0-9]+$", change["range"])) for change in changes)
    print(f"Additional metric updates: {metric_updates}; existing values and formulas preserved.")
    finish(sheets, before, changes, args.apply)


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
