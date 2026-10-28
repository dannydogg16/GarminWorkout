"""Pure planning: preserve formulas and manual content; never guess prescriptions."""
import json
import math
import re
from datetime import date, datetime
from .http import SyncError

HEADERS = ["Date", "Activity ID", "Run type", "Distance (mi)", "Duration (m:ss)",
           "Avg HR (bpm)", "Effort (1–10)", "Notes / source", "Pace (min/mi)", "Week starting"]
CAPACITY = 301  # Progress formulas only include rows 2:301.


def number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise SyncError("Garmin returned an invalid numeric metric; no writes performed.")
    return value


def normalize(item):
    identifier = str(item.get("activityId", ""))
    if not re.fullmatch(r"[1-9][0-9]*", identifier):
        raise SyncError("Invalid Garmin activity ID.")
    try:
        local = datetime.strptime(item["startTimeLocal"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        raise SyncError("Activity has no valid local start time; no writes performed.") from None
    kind = item.get("activityType")
    key = kind.get("typeKey") if isinstance(kind, dict) else None
    if key != "running" and not (isinstance(key, str) and key.endswith("_running")):
        raise SyncError("Garmin returned an unexpected activity type; refusing to import.")
    distance, duration = number(item.get("distance")), number(item.get("duration"))
    miles = None if distance is None else distance / 1609.344
    minutes = None if duration is None else duration / 60
    speed = number(item.get("averageSpeed"))
    extra = {"source": "Garmin Personal API", "local_start": local.isoformat(),
             "max_hr_bpm": number(item.get("maxHR")),
             "avg_pace_min_mi": minutes / miles if miles and minutes is not None else None,
             "garmin_avg_pace_min_mi": 1609.344 / speed / 60 if speed else None}
    for source, target in [("elevationGain", "elevation_gain_m"), ("calories", "calories_kcal"),
                           ("averageRunningCadenceInStepsPerMinute", "cadence_spm"),
                           ("avgPower", "avg_power_w"), ("aerobicTrainingEffect", "aerobic_training_effect"),
                           ("anaerobicTrainingEffect", "anaerobic_training_effect")]:
        extra[target] = number(item.get(source))
    return {"id": identifier, "date": local.date().isoformat(), "local": local.isoformat(),
            "type": key, "miles": miles, "minutes": minutes,
            "hr": number(item.get("averageHR")), "extra": {k: v for k, v in extra.items() if v is not None}}


def validate_prescriptions(entries):
    if not isinstance(entries, list):
        raise SyncError("Prescriptions must be a JSON array.")
    ids = set()
    for p in entries:
        if not isinstance(p, dict) or set(p) - {"id", "date", "run_type", "garmin_workout_name", "activity_id"}:
            raise SyncError("Invalid prescription fields.")
        for field in ("id", "date", "run_type"):
            if not isinstance(p.get(field), str) or not p[field].strip() or len(p[field]) > 200:
                raise SyncError("Prescription needs id, date, and run_type strings.")
        try:
            date.fromisoformat(p["date"])
        except ValueError:
            raise SyncError("Prescription date must be YYYY-MM-DD.") from None
        if p["id"] in ids:
            raise SyncError("Prescription IDs must be unique.")
        ids.add(p["id"])
        for field in ("activity_id", "garmin_workout_name"):
            if field in p and (not isinstance(p[field], str) or not p[field].strip() or len(p[field]) > 200):
                raise SyncError("Invalid prescription matching field.")
        if "activity_id" in p and not re.fullmatch(r"[1-9][0-9]*", p["activity_id"]):
            raise SyncError("Prescription activity_id must be a numeric string.")
        if not p.get("activity_id") and not p.get("garmin_workout_name"):
            raise SyncError("Prescription needs an activity_id or exact garmin_workout_name; dates alone are ambiguous.")
    return entries


def formula(column, row):
    if column == "I":
        return f'=IF(OR(A{row}="",D{row}="",D{row}<=0,E{row}=""),"",E{row}/D{row})'
    return f'=IF(A{row}="","",A{row}-WEEKDAY(A{row},2)+1)'


def compact_formula(value):
    return value.replace(" ", "").upper() if isinstance(value, str) else ""


def existing_id(value):
    if value in (None, ""):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return value
    raise SyncError("Run Log contains an invalid or formula-based Activity ID; resolve it before syncing.")


def plan(rows, activities, prescriptions, workout_names=None):
    if not rows or rows[0][:10] != HEADERS:
        raise SyncError("Live Run Log headers differ from the inspected workbook; no writes performed.")
    workout_names = workout_names or {}
    ids, occupied, used_prescriptions = set(), set(), set()
    for row_no, raw in enumerate(rows[1:], 2):
        row = list(raw) + [""] * (10 - len(raw))
        identifier = existing_id(row[1])
        if identifier:
            if identifier in ids:
                raise SyncError("Run Log already contains duplicate activity IDs; resolve before syncing.")
            ids.add(identifier)
        if any(v not in ("", None) for v in row[:8]):
            occupied.add(row_no)
        if isinstance(row[7], str):
            try:
                note = json.loads(row[7])
                if isinstance(note, dict) and isinstance(note.get("prescription_id"), str):
                    used_prescriptions.add(note["prescription_id"])
            except ValueError:
                pass
    pending = sorted((a for a in activities if a["id"] not in ids), key=lambda a: (a["local"], int(a["id"])))
    # Match one prescription to one actual activity across the whole selected batch.
    candidates = {}
    for a in pending:
        matches = [p for p in prescriptions if p["date"] == a["date"] and p["id"] not in used_prescriptions
                   and ((p.get("activity_id") == a["id"]) if p.get("activity_id") else
                        (p.get("garmin_workout_name") in workout_names.get(a["id"], [])))]
        candidates[a["id"]] = matches
    frequency = {}
    for matches in candidates.values():
        for p in matches:
            frequency[p["id"]] = frequency.get(p["id"], 0) + 1
    changes, imported, matched = [], [], 0
    for a in pending:
        if a["id"] in ids:
            continue
        target = next((r for r in range(2, CAPACITY + 1) if r not in occupied), None)
        if target is None:
            raise SyncError("Run Log's 300-row reporting capacity is full; extend formulas in a separate change.")
        row = list(rows[target - 1]) if target <= len(rows) else []
        row += [""] * max(0, 10 - len(row))
        if any(compact_formula(row[index]) != compact_formula(formula(col, target))
               for index, col in [(8, "I"), (9, "J")]):
            raise SyncError(f"Run Log row {target} has missing or unexpected I/J formulas; restore before syncing.")
        matches = candidates[a["id"]]
        match = matches[0] if len(matches) == 1 and frequency[matches[0]["id"]] == 1 else None
        notes = dict(a["extra"])
        notes["match_status"] = "matched" if match else "unmatched_or_ambiguous"
        if match:
            notes["prescription_id"] = match["id"]
            matched += 1
        serial = (date.fromisoformat(a["date"]) - date(1899, 12, 30)).days
        values = [serial, a["id"], match["run_type"] if match else a["type"],
                  a["miles"], None if a["minutes"] is None else a["minutes"] / 1440, a["hr"]]
        # G (subjective effort), I/J (formulas), other tabs and formatting never written.
        changes += [{"range": f"'Run Log'!A{target}:F{target}", "values": [["" if v is None else v for v in values]]},
                    {"range": f"'Run Log'!H{target}", "values": [[json.dumps(notes, separators=(",", ":"), allow_nan=False)]]}]
        imported.append(a["id"])
        occupied.add(target)
        ids.add(a["id"])
    return changes, imported, matched
