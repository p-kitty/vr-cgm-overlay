"""LibreLinkUp API client.

Talks to the same unofficial endpoints as DiaKEM/libre-link-up-api-client
(which LibreLinkUpDesktop uses) and pylibrelinkup.

Fragile spots in this API, taken from failure reports against those
existing clients:
  1. A stale `version` header gets rejected with a 4xx, so it is
     configurable rather than hardcoded here.
  2. Login can answer with a region redirect, which we follow.
  3. Newer API versions require an `account-id` header holding the SHA256
     of the user id.
  4. Age is computed from FactoryTimestamp (UTC). Timestamp is local time
     with no zone attached, so using it would skew by the UTC offset.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

# Where login starts. Even if this is the wrong region, the response tells
# us which one to use.
DEFAULT_API_URL = "https://api.libreview.io"

REGION_URLS = {
    "eu": "https://api-eu.libreview.io",
    "eu2": "https://api-eu2.libreview.io",
    "us": "https://api.libreview.io",
    "ae": "https://api-ae.libreview.io",
    "ap": "https://api-ap.libreview.io",
    "au": "https://api-au.libreview.io",
    "ca": "https://api-ca.libreview.io",
    "de": "https://api-de.libreview.io",
    "fr": "https://api-fr.libreview.io",
    "jp": "https://api-jp.libreview.io",
    "la": "https://api-la.libreview.io",
    "ru": "https://api.libreview.ru",
}

# TrendArrow value -> arrow used in logs and CLI output. The overlay draws
# its own arrows as vectors instead (see renderer.py).
# 1=falling fast 2=falling 3=flat 4=rising 5=rising fast
TREND_ARROWS = {1: "↓", 2: "↘", 3: "→", 4: "↗", 5: "↑"}


class LibreLinkError(Exception):
    """Any error originating from the LibreLinkUp API."""


class AuthError(LibreLinkError):
    """Authentication failed in a way retrying will not fix."""


class TokenExpired(LibreLinkError):
    """Token expired. Logging in again recovers."""


@dataclass
class Reading:
    """A single glucose measurement."""

    value_mgdl: float
    value_mmol: float
    trend: int
    timestamp_utc: datetime
    is_high: bool
    is_low: bool

    @property
    def arrow(self) -> str:
        return TREND_ARROWS.get(self.trend, "")

    def age_minutes(self, now: datetime | None = None) -> float:
        """Minutes since the measurement, used to decide staleness."""
        now = now or datetime.now(timezone.utc)
        return (now - self.timestamp_utc).total_seconds() / 60.0

    def display_value(self, unit: str) -> str:
        if unit == "mmol":
            return f"{self.value_mmol:.1f}"
        return f"{self.value_mgdl:.0f}"


def _parse_factory_timestamp(raw: str) -> datetime:
    """Parse a FactoryTimestamp.

    The format is M/D/YYYY h:mm:ss AM/PM, e.g. "9/4/2026 3:04:05 PM". No
    zone is given but the value is UTC, so we attach UTC explicitly.
    """
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise LibreLinkError(f"cannot parse FactoryTimestamp: {raw!r}")


class LibreLinkUp:
    """Reads glucose values as a LibreLinkUp follower account.

    Usage:
        client = LibreLinkUp(email, password)
        reading = client.get_latest()   # logs in on demand
    """

    def __init__(
        self,
        email: str,
        password: str,
        *,
        patient_id: str | None = None,
        region: str | None = None,
        version: str = "4.16.0",
        timeout: float = 15.0,
    ) -> None:
        self._email = email
        self._password = password
        self._patient_id = patient_id
        self._version = version
        self._timeout = timeout

        self._api_url = REGION_URLS.get(region or "", DEFAULT_API_URL)
        self._token: str | None = None
        self._account_id_hash: str | None = None
        self._session = requests.Session()

    # -- internal helpers ---------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "accept-encoding": "gzip",
            "cache-control": "no-cache",
            "connection": "Keep-Alive",
            "content-type": "application/json",
            "product": "llu.android",
            "version": self._version,
        }
        if self._token:
            headers["authorization"] = f"Bearer {self._token}"
        if self._account_id_hash:
            headers["account-id"] = self._account_id_hash
        return headers

    def _get(self, path: str) -> dict:
        resp = self._session.get(
            f"{self._api_url}{path}", headers=self._headers(), timeout=self._timeout
        )
        if resp.status_code == 401:
            raise TokenExpired("token has expired")
        if not resp.ok:
            raise LibreLinkError(f"GET {path} returned {resp.status_code}")
        return resp.json()

    # -- authentication -----------------------------------------------------

    def login(self) -> None:
        """Log in and store a token, following a region redirect if given.

        Only one redirect is allowed, to avoid looping.
        """
        for attempt in range(2):
            payload = {"email": self._email, "password": self._password}
            resp = self._session.post(
                f"{self._api_url}/llu/auth/login",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
            if not resp.ok:
                # A stale version header usually surfaces here as a 4xx.
                raise AuthError(
                    f"login returned {resp.status_code}; "
                    f"api_version ({self._version}) in the config may be too old"
                )

            body = resp.json()
            data = body.get("data") or {}

            # Wrong region: retry against the one we were pointed at.
            if data.get("redirect"):
                region = data.get("region", "")
                url = REGION_URLS.get(region)
                if not url:
                    raise AuthError(f"redirected to an unknown region: {region!r}")
                if attempt == 1:
                    raise AuthError("region redirect is looping")
                log.info("redirected to region %s (%s), logging in again", region, url)
                self._api_url = url
                continue

            # No token is issued while terms of use acceptance or email
            # verification are outstanding. Those can only be cleared in
            # the official app or on LibreView.
            step = (data.get("step") or {}).get("type")
            if step:
                raise AuthError(
                    f"the account has an outstanding step (type={step}); "
                    "clear it in the official LibreLinkUp app or on LibreView"
                )

            auth = data.get("authTicket") or {}
            token = auth.get("token")
            if not token:
                raise AuthError("response contained no token")

            self._token = token
            user_id = (data.get("user") or {}).get("id")
            if user_id:
                self._account_id_hash = hashlib.sha256(user_id.encode()).hexdigest()
            log.info("logged in (%s)", self._api_url)
            return

        raise AuthError("login failed")

    # -- data retrieval -----------------------------------------------------

    def get_connections(self) -> list[dict]:
        """Patients being followed. Used to find your own patient id."""
        return self._get("/llu/connections").get("data") or []

    def resolve_patient_id(self) -> str:
        """Decide which patient id to read.

        Uses the configured one if given. Otherwise adopts the only
        connection when there is exactly one; several connections are
        ambiguous, so that is an error for the user to resolve.
        """
        if self._patient_id:
            return self._patient_id

        connections = self.get_connections()
        if not connections:
            raise LibreLinkError(
                "no connections found; check that follower sharing is set up "
                "in the LibreLinkUp app"
            )
        if len(connections) > 1:
            names = ", ".join(
                f"{c.get('firstName', '')} {c.get('lastName', '')} = {c.get('patientId')}"
                for c in connections
            )
            raise LibreLinkError(
                f"several connections found; set patient_id in the config: {names}"
            )

        self._patient_id = connections[0]["patientId"]
        log.info("adopted patient_id automatically: %s", self._patient_id)
        return self._patient_id

    def get_latest(self) -> Reading:
        """Return the most recent measurement, logging in as needed."""
        if not self._token:
            self.login()

        try:
            return self._fetch_latest()
        except TokenExpired:
            log.info("token had expired, logging in again")
            self._token = None
            self.login()
            return self._fetch_latest()

    def _fetch_latest(self) -> Reading:
        patient_id = self.resolve_patient_id()
        body = self._get(f"/llu/connections/{patient_id}/graph")
        connection = (body.get("data") or {}).get("connection") or {}
        measurement = connection.get("glucoseMeasurement")
        if not measurement:
            raise LibreLinkError(
                "no measurement returned; the sensor may still be warming up, "
                "or the phone app may not have scanned it recently"
            )

        return Reading(
            value_mgdl=float(measurement["ValueInMgPerDl"]),
            value_mmol=float(measurement["Value"]),
            trend=int(measurement.get("TrendArrow") or 3),
            timestamp_utc=_parse_factory_timestamp(measurement["FactoryTimestamp"]),
            is_high=bool(measurement.get("isHigh")),
            is_low=bool(measurement.get("isLow")),
        )
