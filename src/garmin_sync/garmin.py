import os
from urllib.parse import urlencode
from .http import ApiError, SyncError

BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
SEARCH_PATH = "/activitylist-service/activities/search/activities"


class Garmin:
    def __init__(self, http):
        self.http = http
        self.key = os.environ.get("GARMIN_API_KEY", "")
        if not self.key:
            raise SyncError("Set GARMIN_API_KEY securely; never pass it as a command argument.")
        self.token = None

    def authenticate(self):
        result = self.http.request("Garmin token", "POST", BASE + TOKEN_PATH,
                                   {"Authorization": "ApiKey " + self.key, "Accept": "*/*"},
                                   allow_text=True)
        # The public guide does not specify a response schema. Accept text or
        # recognized OAuth fields; forcing JSON negotiation causes HTTP 406.
        token = ((result.get("access_token") or result.get("accessToken"))
                 if isinstance(result, dict) else result if isinstance(result, str) else None)
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise SyncError("Garmin token response has no recognized access token; check current official docs.")
        self.token = token

    def get(self, path):
        if self.token is None:
            self.authenticate()
        try:
            return self.http.request("Garmin", "GET", BASE + path,
                                     {"Authorization": "Bearer " + self.token})
        except ApiError as error:
            if error.status != 401:
                raise
            self.authenticate()
            return self.http.request("Garmin", "GET", BASE + path,
                                     {"Authorization": "Bearer " + self.token})

    def activities(self, since, until):
        found = {}
        for start in range(0, 10_000, 99):
            query = urlencode({"start": start, "limit": 99, "activityType": "running",
                               "startDate": since, "endDate": until, "sortBy": "startLocal",
                               "sortOrder": "desc", "excludeChildren": "true"})
            page = self.get(SEARCH_PATH + "?" + query)
            if not isinstance(page, list):
                raise SyncError("Garmin activity response must be an array.")
            for item in page:
                if not isinstance(item, dict):
                    raise SyncError("Garmin returned an invalid activity.")
                identifier = str(item.get("activityId", ""))
                if not identifier.isascii() or not identifier.isdigit() or int(identifier) <= 0:
                    raise SyncError("Garmin returned an invalid activity ID.")
                found[identifier] = item
            if len(page) < 99:
                return list(found.values())
        raise SyncError("Garmin pagination exceeded safety limit; choose a shorter date range.")

    def workouts(self, identifier):
        result = self.get(f"/activity-service/activity/{identifier}/workouts")
        if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
            raise SyncError("Garmin workout response must be an array of objects.")
        return result

    def summary(self, identifier):
        result = self.get(f"/activity-service/activity/{identifier}")
        summary = result.get("summaryDTO") if isinstance(result, dict) else None
        if not isinstance(summary, dict):
            raise SyncError("Garmin detailed activity has no valid summaryDTO.")
        return summary
