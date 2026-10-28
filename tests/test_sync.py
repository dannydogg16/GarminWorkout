import copy
import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from garmin_sync.http import JsonHttp, NoRedirect, SyncError, ApiError
from garmin_sync.garmin import Garmin, recorded_effort
from garmin_sync.planner import HEADERS, formula, normalize, plan, validate_prescriptions
from garmin_sync.sheets import Sheets
from garmin_sync.__main__ import run


def activity(identifier=123, **kw):
    return {"activityId": identifier, "startTimeLocal": "2026-10-27 07:00:00",
            "activityType": {"typeKey": "running"}, "distance": 1609.344,
            "duration": 600, "averageHR": 140, "maxHR": 170, **kw}


def rows(count=300):
    return [HEADERS[:]] + [[""] * 8 + [formula("I", r), formula("J", r)] for r in range(2, count + 2)]


class PlannerTests(unittest.TestCase):
    def test_recorded_effort_on_new_activity(self):
        a = normalize(activity()); a["effort"] = 7
        changes, ids, _ = plan(rows(), [a], [])
        self.assertEqual(ids, ["123"])
        self.assertEqual(changes[-1], {"range": "'Run Log'!G2", "values": [[7]]})

    def test_late_effort_fills_existing_blank_without_duplicate_or_other_edits(self):
        before = rows(); before[1][:6] = [46000, "123", "running", 5, 0.03, 140]
        before[1][7] = "Preserve these notes"
        a = normalize(activity()); a["effort"] = 7
        changes, ids, _ = plan(before, [a, a], [])
        self.assertEqual(ids, [])
        self.assertEqual(changes, [{"range": "'Run Log'!G2", "values": [[7]]}])
        self.assertEqual(before[1][7], "Preserve these notes")

    def test_existing_effort_never_overwritten(self):
        for value in (6, "manual rating", "=5+1", 0):
            before = rows(); before[1][1] = "123"; before[1][6] = value
            a = normalize(activity()); a["effort"] = 7
            self.assertEqual(plan(before, [a], [])[0], [])

    def test_units_and_formula_preservation(self):
        before = rows(); original = copy.deepcopy(before)
        changes, ids, matches = plan(before, [normalize(activity())], [])
        self.assertEqual(before, original)
        self.assertEqual(ids, ["123"]); self.assertEqual(matches, 0)
        self.assertEqual([c["range"] for c in changes], ["'Run Log'!A2:F2", "'Run Log'!H2"])
        self.assertEqual(changes[0]["values"][0][1:], ["123", "running", 1, 10 / 1440, 140])
        notes = json.loads(changes[1]["values"][0][0])
        self.assertEqual(notes["max_hr_bpm"], 170); self.assertEqual(notes["avg_pace_min_mi"], 10)

    def test_missing_metrics_are_blank_not_zero(self):
        changes, _, _ = plan(rows(), [normalize(activity(distance=None, duration=None, averageHR=None, maxHR=None))], [])
        self.assertEqual(changes[0]["values"][0][3:], ["", "", ""])
        self.assertNotIn("max_hr_bpm", json.loads(changes[1]["values"][0][0]))

    def test_rerun_deduplicates_after_partial_network_failure(self):
        before = rows(); a = normalize(activity()); changes, _, _ = plan(before, [a], [])
        before[1][:6] = changes[0]["values"][0]; before[1][7] = changes[1]["values"][0][0]
        self.assertEqual(plan(before, [a], [])[0], [])

    def test_numeric_existing_ids(self):
        for val in (123, 123.0, "123"):
            before = rows(); before[1][1] = val
            self.assertEqual(plan(before, [normalize(activity())], [])[0], [])

    def test_duplicate_source_ids_only_one_import(self):
        a = normalize(activity())
        self.assertEqual(len(plan(rows(), [a, a], [])[1]), 1)

    def test_duplicate_destination_ids_fail_closed(self):
        before = rows(); before[1][1] = before[2][1] = "123"
        with self.assertRaises(SyncError): plan(before, [], [])

    def test_manual_content_and_effort_not_overwritten(self):
        for col in range(8):
            before = rows(); before[1][col] = "123" if col == 1 else "manual"
            changes, _, _ = plan(before, [normalize(activity(456))], [])
            self.assertEqual(changes[0]["range"], "'Run Log'!A3:F3")

    def test_capacity_is_reporting_limit_not_formatted_rows(self):
        before = rows(999)
        for row in before[1:301]: row[0] = 45000
        with self.assertRaisesRegex(SyncError, "capacity"): plan(before, [normalize(activity())], [])

    def test_changed_headers_or_formulas_stop_write(self):
        before = rows(); before[0][3] = "Distance (km)"
        with self.assertRaises(SyncError): plan(before, [normalize(activity())], [])
        before = rows(); before[1][8] = ""
        with self.assertRaises(SyncError): plan(before, [normalize(activity())], [])

    def test_bad_id_date_type_or_metric(self):
        for kw in [{"activityId": "=evil()"}, {"startTimeLocal": None},
                   {"activityType": {"typeKey": "cycling"}}, {"distance": -1},
                   {"distance": float("nan")}, {"maxHR": True}]:
            with self.subTest(kw=kw), self.assertRaises(SyncError): normalize(activity(**kw))

    def test_subtype_and_activity_local_day(self):
        a = normalize(activity(activityType={"typeKey": "trail_running"}, startTimeLocal="2026-10-26 23:59:00"))
        self.assertEqual(a["date"], "2026-10-26")

    def test_exact_prescription_and_raw_text(self):
        p = {"id": "work-1", "date": "2026-10-27", "run_type": "=literal text", "activity_id": "123"}
        changes, _, matched = plan(rows(), [normalize(activity())], validate_prescriptions([p]))
        self.assertEqual(matched, 1)
        self.assertEqual(changes[0]["values"][0][2], "=literal text")  # RAW Sheets writes, never USER_ENTERED
        self.assertEqual(json.loads(changes[1]["values"][0][0])["prescription_id"], "work-1")

    def test_workout_name_match_requires_date_and_unique_actual(self):
        p = {"id": "work-1", "date": "2026-10-27", "run_type": "Tempo", "garmin_workout_name": "Tempo 20"}
        a, b = normalize(activity()), normalize(activity(456))
        self.assertEqual(plan(rows(), [a], [p], {"123": ["Tempo 20"]})[2], 1)
        self.assertEqual(plan(rows(), [a, b], [p], {"123": ["Tempo 20"], "456": ["Tempo 20"]})[2], 0)
        self.assertEqual(plan(rows(), [a], [p], {"123": ["tempo 20"]})[2], 0)

    def test_multiple_prescriptions_are_ambiguous(self):
        base = {"date": "2026-10-27", "run_type": "Tempo", "activity_id": "123"}
        self.assertEqual(plan(rows(), [normalize(activity())], [{"id": "a", **base}, {"id": "b", **base}])[2], 0)

    def test_consumed_prescription_is_not_reused(self):
        before = rows(); before[1][7] = '{"prescription_id":"a"}'
        p = {"id": "a", "date": "2026-10-27", "run_type": "Tempo", "activity_id": "123"}
        self.assertEqual(plan(before, [normalize(activity())], [p])[2], 0)

    def test_prescription_validation(self):
        for p in ({}, [{"id": "a", "date": "2026-10-27", "run_type": "easy"}],
                  [{"id": "a", "date": "bad", "run_type": "easy", "activity_id": "123"}]):
            with self.assertRaises(SyncError): validate_prescriptions(p)


class HttpTests(unittest.TestCase):
    def test_token_plain_text_is_opt_in(self):
        opener = Mock(); response = Mock(); response.read.return_value = b'test-only-token'
        context = Mock(); context.__enter__ = Mock(return_value=response); context.__exit__ = Mock(return_value=False)
        opener.open.return_value = context
        http = JsonHttp(opener)
        with self.assertRaises(SyncError): http.request("test", "POST", "https://example.com", {})
        self.assertEqual(http.request("test", "POST", "https://example.com", {}, allow_text=True), "test-only-token")

    def test_retry_rate_limit_then_success(self):
        opener = Mock(); result = Mock(); result.read.return_value = b'{"ok":true}'
        response = Mock(); response.__enter__ = Mock(return_value=result); response.__exit__ = Mock(return_value=False)
        opener.open.side_effect = [HTTPError("https://example.com", 429, "", {"Retry-After": "2"}, None), response]
        sleep = Mock(); http = JsonHttp(opener, sleep)
        self.assertEqual(http.request("test", "GET", "https://example.com", {}), {"ok": True})
        sleep.assert_called_once_with(2)

    def test_errors_never_expose_service_body(self):
        opener = Mock(); opener.open.side_effect = HTTPError("https://example.com", 401, "SECRET", {}, io.BytesIO(b'SECRET'))
        with self.assertRaises(ApiError) as caught:
            JsonHttp(opener).request("test", "GET", "https://example.com", {"Authorization": "SECRET"})
        self.assertNotIn("SECRET", str(caught.exception)); self.assertEqual(opener.open.call_count, 1)

    def test_network_retries_bounded(self):
        opener = Mock(); opener.open.side_effect = URLError("SECRET")
        with self.assertRaises(SyncError) as caught:
            JsonHttp(opener, Mock()).request("test", "GET", "https://example.com", {})
        self.assertNotIn("SECRET", str(caught.exception)); self.assertEqual(opener.open.call_count, 4)

    def test_long_retry_after_stops_without_hammering(self):
        opener = Mock(); sleep = Mock()
        opener.open.side_effect = HTTPError("https://example.com", 429, "", {"Retry-After": "3600"}, None)
        with self.assertRaises(SyncError): JsonHttp(opener, sleep).request("test", "GET", "https://example.com", {})
        sleep.assert_not_called()

    def test_no_redirect_authorization_forwarding(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_no_plain_http(self):
        with self.assertRaises(SyncError): JsonHttp().request("test", "GET", "http://example.com", {})


class GarminTests(unittest.TestCase):
    def test_recorded_rpe_scale_and_unavailable_values(self):
        for raw, expected in [(10, 1), (70, 7), (100, 10), (25, 2.5)]:
            self.assertEqual(recorded_effort({"directWorkoutRpe": raw}), expected)
        for value in (None, 0, -1, 5, 101, "70", True, float("nan")):
            self.assertIsNone(recorded_effort({"directWorkoutRpe": value}))
        self.assertIsNone(recorded_effort({"perceivedWorkoutEffort": 70, "aerobicTrainingEffect": 4}))

    @patch.dict(os.environ, {"GARMIN_API_KEY": "test-only-key"})
    def test_detail_summary_endpoint(self):
        http = Mock(); http.request.side_effect = [{"access_token": "one"}, {"summaryDTO": {"directWorkoutRpe": 70}}]
        self.assertEqual(Garmin(http).summary("123"), {"directWorkoutRpe": 70})
        self.assertTrue(http.request.call_args.args[2].endswith("/activity-service/activity/123"))

    @patch.dict(os.environ, {"GARMIN_API_KEY": "test-only-key"})
    def test_token_accepts_plain_text_and_does_not_require_json(self):
        http = Mock(); http.request.return_value = "test-only-token"
        client = Garmin(http); client.authenticate()
        self.assertEqual(client.token, "test-only-token")
        self.assertEqual(http.request.call_args.args[3]["Accept"], "*/*")
        self.assertTrue(http.request.call_args.kwargs["allow_text"])

    @patch.dict(os.environ, {"GARMIN_API_KEY": "test-only-key"})
    def test_expired_token_renewed_once(self):
        http = Mock(); http.request.side_effect = [{"access_token": "one"}, ApiError("Garmin", 401), {"accessToken": "two"}, []]
        self.assertEqual(Garmin(http).activities("2026-10-01", "2026-10-27"), [])
        self.assertEqual(http.request.call_count, 4)
        self.assertEqual(http.request.call_args.args[3], {"Authorization": "Bearer two"})

    @patch.dict(os.environ, {"GARMIN_API_KEY": "test-only-key"})
    def test_paginate_and_deduplicate(self):
        http = Mock(); http.request.side_effect = [{"access_token": "one"}, [activity(i) for i in range(1, 100)], [activity(99), activity(100)]]
        result = Garmin(http).activities("2026-10-01", "2026-10-27")
        self.assertEqual(len(result), 100)
        self.assertIn("start=99", http.request.call_args.args[2])
        self.assertIn("activityType=running", http.request.call_args.args[2])

    @patch.dict(os.environ, {"GARMIN_API_KEY": "test-only-key"})
    def test_token_schema_failure_sanitized(self):
        http = Mock(); http.request.return_value = {"error": "SECRET"}
        with self.assertRaises(SyncError) as caught: Garmin(http).authenticate()
        self.assertNotIn("SECRET", str(caught.exception))


class SheetsTests(unittest.TestCase):
    def test_minimum_scopes(self):
        for write, scope in [(False, "https://www.googleapis.com/auth/spreadsheets.readonly"),
                             (True, "https://www.googleapis.com/auth/spreadsheets")]:
            with patch("google.auth.default") as default:
                creds = Mock(); creds.token = "fake-token"
                default.return_value = (creds, None)
                Sheets(Mock(), write=write)
                default.assert_called_once_with(scopes=[scope])
                creds.refresh.assert_called_once()

    def client(self, write):
        obj = Sheets.__new__(Sheets); obj.write = write; obj.read = Mock(return_value=rows()); obj.request = Mock()
        return obj

    def test_dry_run_cannot_write(self):
        obj = self.client(False)
        with self.assertRaises(SyncError): obj.apply(rows(), [{"range": "anything"}])
        obj.request.assert_not_called()

    def test_detect_concurrent_edit(self):
        obj = self.client(True); before = rows(); obj.read.return_value[1][0] = "edited"
        with self.assertRaises(SyncError): obj.apply(before, [{"range": "anything"}])
        obj.request.assert_not_called()

    def test_raw_fixed_ranges_and_retry_safe_payload(self):
        obj = self.client(True); before = rows()
        changes, _, _ = plan(before, [normalize(activity())], [])
        obj.apply(before, changes)
        self.assertEqual(obj.request.call_args.args, ("POST", "/values:batchUpdate", {"valueInputOption": "RAW", "data": changes}))


class CliTests(unittest.TestCase):
    def test_live_effort_backfill_readback_uses_column_g(self):
        before = rows(); before[1][1] = "123"
        after = copy.deepcopy(before); after[1][6] = 7
        with patch("garmin_sync.__main__.Garmin") as garmin, patch("garmin_sync.__main__.Sheets") as sheets, \
             patch("sys.argv", ["sync", "--latest", "--apply"]), patch("sys.stdout", new_callable=io.StringIO) as output:
            garmin.return_value.activities.return_value = [activity()]
            garmin.return_value.summary.return_value = {"directWorkoutRpe": 70}
            sheets.return_value.read.side_effect = [before, after]
            run()
            sheets.return_value.apply.assert_called_once_with(before, [{"range": "'Run Log'!G2", "values": [[7]]}])
            self.assertIn("Recorded effort updates: 1", output.getvalue())
            self.assertIn("Live write verified", output.getvalue())

    def test_apply_latest_and_repeat_preserves_formulas(self):
        state = rows(); before_formulas = [r[8:] for r in state[1:]]
        client = Mock(); client.read.side_effect = lambda: copy.deepcopy(state)
        def apply(before, changes):
            self.assertEqual(before, state)
            for change in changes:
                import re
                row = int(re.search(r"[A-Z](\d+)", change["range"])[1]) - 1
                start = 0 if "!A" in change["range"] else 7
                vals = change["values"][0]; state[row][start:start + len(vals)] = vals
        client.apply.side_effect = apply
        with patch("garmin_sync.__main__.Garmin") as garmin, patch("garmin_sync.__main__.Sheets", return_value=client), \
             patch("sys.argv", ["sync", "--latest", "--apply"]), patch("sys.stdout", new_callable=io.StringIO) as output:
            garmin.return_value.activities.return_value = [activity(111, startTimeLocal="2026-10-26 07:00:00"), activity(222, averageHR=None)]
            run(); run()
            self.assertEqual(state[1][1], "222"); self.assertEqual(state[2][1], "")
            self.assertEqual(state[1][5], "")
            self.assertEqual([r[8:] for r in state[1:]], before_formulas)
            self.assertIn("Live write verified", output.getvalue())
            self.assertIn("already present 1", output.getvalue())

    def test_latest_dry_run_never_applies(self):
        with patch("garmin_sync.__main__.Garmin") as garmin, patch("garmin_sync.__main__.Sheets") as sheets, \
             patch("sys.argv", ["sync", "--latest"]), patch("sys.stdout", new_callable=io.StringIO):
            garmin.return_value.activities.return_value = [activity()]
            sheets.return_value.read.return_value = rows()
            run()
            sheets.assert_called_once_with(unittest.mock.ANY, write=False)
            sheets.return_value.apply.assert_not_called()

    def test_no_activity_is_not_a_live_test(self):
        with patch("garmin_sync.__main__.Garmin") as garmin, patch("garmin_sync.__main__.Sheets") as sheets, \
             patch("sys.argv", ["sync", "--latest"]), patch("sys.stdout", new_callable=io.StringIO) as output:
            garmin.return_value.activities.return_value = []
            run(); sheets.assert_not_called()
            self.assertIn("No running activities found", output.getvalue())


class TimeFormatTests(unittest.TestCase):
    def book(self):
        from garmin_sync.time_format import progress_formulas
        book = {"Run Log": rows(), "Progress": [[""] * 6 for _ in range(34)], "Profile": [["Keep this"]]}
        book["Run Log"][0][4] = "Duration (min)"
        book["Run Log"][1][4] = 42.2781656901042
        for cell, value in progress_formulas().items():
            book["Progress"][int(cell[1:]) - 1][ord(cell[0]) - 65] = value
        return book, {"Run Log": 1, "Progress": 2, "Profile": 3}

    def convert(self, book, ids, requests):
        after = copy.deepcopy(book)
        for request in requests:
            if "updateCells" not in request: continue
            update = request["updateCells"]; area = update["range"]
            tab = next(t for t, i in ids.items() if i == area["sheetId"])
            value = next(iter(update["rows"][0]["values"][0]["userEnteredValue"].values()))
            after[tab][area["startRowIndex"]][area["startColumnIndex"]] = value
        return after

    def test_atomic_conversion_preserves_totals_and_formulas(self):
        from garmin_sync.time_format import migration, FORMAT
        book, ids = self.book(); original = copy.deepcopy(book)
        requests, minutes, converted = migration(book, ids)
        self.assertFalse(converted); self.assertEqual(book, original)
        after = self.convert(book, ids, requests)
        self.assertEqual(after["Run Log"][1][4], book["Run Log"][1][4] / 1440)
        self.assertEqual(after["Progress"][5][1], "=SUM('Run Log'!E2:E301)*1440")
        self.assertEqual(after["Run Log"][1][8:], book["Run Log"][1][8:])
        self.assertEqual(after["Profile"], book["Profile"])
        self.assertAlmostEqual(after["Run Log"][1][4] * 1440, minutes[2])
        self.assertEqual([r["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"] for r in requests if "repeatCell" in r], [FORMAT, FORMAT])
        _, after_minutes, converted = migration(after, ids)
        self.assertTrue(converted); self.assertAlmostEqual(minutes[2], after_minutes[2])

    def test_repeat_does_not_divide_again(self):
        from garmin_sync.time_format import migration
        book, ids = self.book(); requests, _, _ = migration(book, ids)
        after = self.convert(book, ids, requests)
        repeated, _, converted = migration(after, ids)
        self.assertTrue(converted); self.assertTrue(all("repeatCell" in r for r in repeated))

    def test_unexpected_dependencies_or_text_duration_stop_migration(self):
        from garmin_sync.time_format import migration
        for tab, r, c, value in [("Profile", 0, 0, "=SUM('Run Log'!E2:E301)"),
                                  ("Run Log", 1, 4, "42:17"), ("Progress", 5, 1, "=1")]:
            book, ids = self.book(); book[tab][r][c] = value
            with self.assertRaises(SyncError): migration(book, ids)


if __name__ == "__main__": unittest.main()
