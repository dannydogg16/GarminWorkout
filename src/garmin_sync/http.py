"""Bounded HTTPS JSON requests. Never include response bodies or credentials in errors."""
import json
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class SyncError(Exception):
    pass


class ApiError(SyncError):
    def __init__(self, service, status):
        self.status = status
        advice = {
            401: "Authentication failed; check credential expiry.",
            403: "Access denied; check account permissions and API enablement.",
            429: "Rate limit reached; retry on the next run.",
        }.get(status, "Request failed; rerun after checking service availability.")
        super().__init__(f"{service}: HTTP {status}. {advice}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Authorization must never follow redirects to a different destination.
        return None


class JsonHttp:
    def __init__(self, opener=None, sleep=time.sleep):
        self.opener = opener or build_opener(NoRedirect())
        self.sleep = sleep

    def request(self, service, method, url, headers, body=None, *, allow_text=False):
        if not url.startswith("https://"):
            raise SyncError("HTTPS is required.")
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        for attempt in range(4):
            req = Request(url, data=data, method=method,
                          headers={"Accept": "application/json", **headers})
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with self.opener.open(req, timeout=30) as response:
                    raw = response.read(10_000_001)
                if len(raw) > 10_000_000:
                    raise SyncError(f"{service}: response exceeds safety limit.")
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeError):
                    if allow_text and len(raw) <= 65_536:
                        try:
                            return raw.decode("utf-8").strip()
                        except UnicodeError:
                            pass
                    raise SyncError(f"{service}: invalid JSON response.") from None
            except HTTPError as error:
                status = error.code
                retry = error.headers.get("Retry-After")
                error.close()
                if status not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise ApiError(service, status) from None
                delay = self.retry_delay(retry, attempt)
                if delay > 60:
                    raise SyncError(f"{service}: HTTP {status}; server requested {delay:.0f}s cooldown; rerun after cooldown.")
                self.sleep(delay)
            except (URLError, TimeoutError, OSError):
                if attempt == 3:
                    raise SyncError(f"{service}: network failure after bounded retries.") from None
                self.sleep(2 ** attempt + random.random())

    @staticmethod
    def retry_delay(value, attempt):
        if value:
            try:
                return max(0, float(value))
            except ValueError:
                try:
                    return max(0, (parsedate_to_datetime(value) -
                                   datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return 2 ** attempt + random.random()
