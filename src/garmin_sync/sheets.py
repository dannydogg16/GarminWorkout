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
