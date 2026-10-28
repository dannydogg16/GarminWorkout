"""Atomic, guarded migration from decimal minutes to native Sheets durations."""
import math
from .http import SyncError
from .planner import HEADERS, CAPACITY, compact_formula, formula

FORMAT = {"type": "TIME", "pattern": "[m]:ss"}


def progress_formulas():
    result = {"B4": '=COUNTIFS(\'Run Log\'!A2:A301,">0",\'Run Log\'!D2:D301,">0")',
              "B5": "=SUM('Run Log'!D2:D301)", "B6": "=SUM('Run Log'!E2:E301)"}
    for r in range(18, 34):
        for col, source in [("D", "D"), ("E", "E")]:
            result[f"{col}{r}"] = f"=SUMIF('Run Log'!J$2:J$301,A{r},'Run Log'!{source}$2:{source}$301)"
    return result


def address(row, column):
    letters = ""
    while column:
        column, digit = divmod(column - 1, 26)
        letters = chr(65 + digit) + letters
    return f"{letters}{row}"


def migration(book, sheet_ids):
    """Return an atomic batch, expected minute totals, and old/new mode.

    book contains formula-rendered values, never credentials. Refuse unknown
    dependencies rather than rewriting user formulas heuristically.
    """
    if "Run Log" not in book or "Progress" not in book:
        raise SyncError("Time formatting requires Run Log and Progress tabs.")
    rows = book["Run Log"]
    headers = rows[0][:10] if rows else []
    old_headers = HEADERS[:]; old_headers[4] = "Duration (min)"
    if headers not in (HEADERS, old_headers):
        raise SyncError("Unexpected Run Log headers; formatting stopped.")
    converted = headers == HEADERS
    expected = progress_formulas()
    duration_cells = {"B6", *(f"E{r}" for r in range(18, 34))}
    for tab, values in book.items():
        for r, row in enumerate(values, 1):
            for c, value in enumerate(row, 1):
                if not isinstance(value, str) or not value.startswith("="):
                    continue
                cell = address(r, c)
                if tab == "Run Log":
                    if c not in (9, 10) or compact_formula(value) != compact_formula(formula("I" if c == 9 else "J", r)):
                        raise SyncError("Unexpected Run Log formula; formatting stopped.")
                if "RUN LOG" in value.upper() or "RUNLOG!" in value.upper():
                    wanted = expected.get(cell) if tab == "Progress" else None
                    if wanted and converted and cell in duration_cells:
                        wanted += "*1440"
                    if not wanted or compact_formula(value) != compact_formula(wanted):
                        raise SyncError("Unexpected formula referencing Run Log; review its units before formatting.")
    # Require every known summary to exist, even if a user replaced it with a constant.
    for cell, original in expected.items():
        import re
        match = re.fullmatch(r"([A-Z])([0-9]+)", cell)
        r, c = int(match[2]) - 1, ord(match[1]) - 65
        values = book["Progress"]
        value = values[r][c] if r < len(values) and c < len(values[r]) else None
        wanted = original + ("*1440" if converted and cell in duration_cells else "")
        if compact_formula(value) != compact_formula(wanted):
            raise SyncError("Progress summary formula differs from the inspected workbook; formatting stopped.")
    requests, minutes = [], {}
    def update(tab, row, col, value):
        entered = {"formulaValue": value} if isinstance(value, str) and value.startswith("=") else (
            {"stringValue": value} if isinstance(value, str) else {"numberValue": value})
        requests.append({"updateCells": {"range": {"sheetId": sheet_ids[tab], "startRowIndex": row - 1,
                         "endRowIndex": row, "startColumnIndex": col - 1, "endColumnIndex": col},
                         "rows": [{"values": [{"userEnteredValue": entered}]}], "fields": "userEnteredValue"}})
    for r, row in enumerate(rows[1:], 2):
        value = row[4] if len(row) > 4 else ""
        if value in (None, ""):
            continue
        if r > CAPACITY or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise SyncError("Duration must be a nonnegative number within the reporting range; formatting stopped.")
        minutes[r] = value * 1440 if converted else value
        if not converted:
            update("Run Log", r, 5, value / 1440)
    if not converted:
        update("Run Log", 1, 5, HEADERS[4])
        for cell in sorted(duration_cells):
            r = int(cell[1:]); c = ord(cell[0]) - 64
            update("Progress", r, c, expected[cell] + "*1440")
    for col in (4, 8):  # E and I only; no formula changes in Run Log.
        requests.append({"repeatCell": {"range": {"sheetId": sheet_ids["Run Log"], "startRowIndex": 1,
                         "endRowIndex": CAPACITY, "startColumnIndex": col, "endColumnIndex": col + 1},
                         "cell": {"userEnteredFormat": {"numberFormat": FORMAT}},
                         "fields": "userEnteredFormat.numberFormat"}})
    return requests, minutes, converted
