from urllib.parse import urlencode, quote
from .http import SyncError

SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SHEET_ID = "1YThajlerH-ftK-GfINcCCgi9arqCNLWOycBIvbbFJUA"


class Sheets:
    def __init__(self, http, write=False):
        import google.auth
        from google.auth.transport.requests import Request
        self.http, self.write = http, write
        self.base = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
        try:
            scope = SCOPE if write else SCOPE + ".readonly"
            credentials, _ = google.auth.default(scopes=[scope])
            credentials.refresh(Request())
            self.token = credentials.token
        except Exception:
            raise SyncError("Google authentication failed; check Workload Identity Federation configuration.") from None

    def request(self, method, suffix, body=None):
        return self.http.request("Google Sheets", method, self.base + suffix,
                                 {"Authorization": "Bearer " + self.token}, body)

    def read(self):
        query = urlencode({"valueRenderOption": "FORMULA", "dateTimeRenderOption": "SERIAL_NUMBER"})
        result = self.request("GET", "/values/" + quote("'Run Log'!A:J", safe="") + "?" + query)
        if not isinstance(result, dict) or not isinstance(result.get("values"), list):
            raise SyncError("Google Sheets returned no Run Log values.")
        return result["values"]

    def apply(self, before, changes):
        if not self.write:
            raise SyncError("Write requires explicit --apply.")
        if not changes:
            return
        if self.read() != before:
            raise SyncError("Run Log changed during planning; rerun to avoid overwriting edits.")
        # Fixed-range updates are idempotent even if a network timeout follows commit.
        # Re-read and plan on the next invocation; never blindly append.
        self.request("POST", "/values:batchUpdate", {"valueInputOption": "RAW", "data": changes})

    def book(self, render="FORMULA"):
        metadata = self.request("GET", "?fields=sheets.properties(sheetId,title)")
        ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in metadata["sheets"]}
        query = [("ranges", "'" + name.replace("'", "''") + "'") for name in ids]
        query += [("valueRenderOption", render), ("dateTimeRenderOption", "SERIAL_NUMBER")]
        result = self.request("GET", "/values:batchGet?" + urlencode(query))
        return {name: value.get("values", []) for name, value in zip(ids, result["valueRanges"])}, ids

    def format_times(self):
        from .time_format import migration
        before, ids = self.book()
        requests, minutes, converted = migration(before, ids)
        if not self.write:
            print(f"Time-format dry run passed for {len(minutes)} recorded durations; no writes performed.")
            return
        computed_before, _ = self.book("UNFORMATTED_VALUE")
        # One atomic batch changes units, summaries, header and display together.
        if self.book() != (before, ids):
            raise SyncError("Spreadsheet changed during time-format planning; rerun.")
        self.request("POST", ":batchUpdate", {"requests": requests})
        after, after_ids = self.book()
        _, after_minutes, now_converted = migration(after, after_ids)
        if not now_converted or minutes.keys() != after_minutes.keys() or any(
                abs(value - after_minutes[row]) > 1e-9 for row, value in minutes.items()):
            raise SyncError("Duration-unit readback failed; inspect version history before retrying.")
        # Assert unrelated values and Run Log formulas survived the migration.
        allowed_progress = {"B6", *(f"E{r}" for r in range(18, 34))}
        computed_after, _ = self.book("UNFORMATTED_VALUE")
        for cell in allowed_progress:
            r, c = int(cell[1:]) - 1, ord(cell[0]) - 65
            old = computed_before["Progress"][r][c]
            new = computed_after["Progress"][r][c]
            if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or abs(old - new) > 1e-8:
                raise SyncError("Progress minute total changed during time formatting; inspect version history.")
        from .time_format import address
        for tab, values in before.items():
            for r, row in enumerate(values, 1):
                for c, value in enumerate(row, 1):
                    if tab == "Run Log" and c == 5:
                        continue
                    if tab == "Progress" and address(r, c) in allowed_progress:
                        continue
                    actual = after[tab][r - 1][c - 1] if r <= len(after[tab]) and c <= len(after[tab][r - 1]) else ""
                    if actual != value:
                        raise SyncError("Unrelated cell changed during formatting; inspect version history.")
        formats = self.request("GET", "?" + urlencode({"ranges": "'Run Log'!E2:I301", "includeGridData": "true",
            "fields": "sheets.data.rowData.values(userEnteredFormat.numberFormat,effectiveValue,formattedValue)"}))
        from .time_format import FORMAT
        cells = formats["sheets"][0].get("data", [{}])[0].get("rowData", [])
        for row in cells:
            for c in (0, 4):
                cell = row.get("values", [])[c] if len(row.get("values", [])) > c else {}
                if cell.get("userEnteredFormat", {}).get("numberFormat") != FORMAT:
                    raise SyncError("Time format readback failed.")
                value = cell.get("effectiveValue", {}).get("numberValue")
                if value is not None:
                    import math
                    seconds = math.floor(value * 86400 + 0.5)
                    if cell.get("formattedValue") != f"{seconds // 60}:{seconds % 60:02d}":
                        raise SyncError("Minute:second display verification failed.")
        print(f"Minute:second formatting verified; {len(minutes)} recorded durations preserved; summary totals remain in minutes.")
