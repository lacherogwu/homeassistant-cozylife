"""CozyLife's cloud HTTP API -- login and device list.

The integration needs this for one thing: discovering each device's
``device_key`` and its assigned relay host/port. Those are what the relay
transport authenticates with, and there is no other way to obtain them.

Several details here are non-obvious and were established by decompiling
the vendor's Android app rather than guessed. Each is commented where it
appears, because getting any of them wrong produces an error that looks
like bad credentials rather than a malformed request. The matching tests
in ``tests/test_account.py`` pin every one of them down.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import AuthError, CozyLifeError

API_BASE = "https://api-us.doiting.com"
LOGIN_PATH = "/api/app/user/login"
DEVICE_LIST_PATH = "/api/v2/app/device_with_group/list"

PACKAGE_NAME = "app.cozylife.app"

# Confirmed live against the unauthenticated
# GET /api/app/user/term?package_name=app.cozylife.app
USER_TERM_VERSION = "1.0.0"
USER_PRIVACY_VERSION = "1.0.0"

# Cloudflare fronts this API and rejects Python's default
# "Python-urllib/3.x" with a 403 (error 1010) before the request reaches
# CozyLife at all. It is a bot filter, not an authentication failure,
# though it looks exactly like one. Any app-shaped agent string clears it.
USER_AGENT = "CozyLife/1.20.24 okhttp/4.9.0"

# Sent by the app on every login. The server's own required-field list
# does not mention it, but omitting it makes a *correct* login fail with an
# unhandled HTTP 500 -- the failure is in the success path, past validation,
# so probing with a deliberately wrong password never reveals it. Same trap
# as the coordinates below; both cost a real login attempt to find.
PACKAGE_VERSION = "1200240553"

# The server geolocates a *successful* login to choose a relay, and throws
# an unhandled 500 when the coordinates resolve to no country -- which 0,0
# ("Null Island") does. A failed login never reaches that code path, so
# probing with a deliberately wrong password never reveals this. Any
# plausible coordinate works; accuracy is irrelevant.
DEFAULT_COORDINATES = (35.1856, 33.3823)

_TIMEOUT = 20.0

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CozyLifeDevice:
    """One device as the account knows it."""

    device_id: str
    device_key: str = field(repr=False)  # control credential: keep it out of logs
    name: str
    relay_host: str
    relay_port: int
    local_ip: str | None = None
    model: str | None = None
    dpids: tuple[int, ...] = ()
    online: bool = True


class CozyLifeAccount:
    """Read-only client for a CozyLife account.

    Nothing here can change a device's state -- it only logs in and lists
    what the account owns.
    """

    def __init__(self, base_url: str = API_BASE, *, timeout: float = _TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def login(
        self,
        email: str,
        password: str,
        *,
        coordinates: tuple[float, float] = DEFAULT_COORDINATES,
    ) -> str:
        """Exchange account credentials for a session token.

        The password is sent in plaintext over TLS, which is what the app
        itself does: its only hashing utility is used for a device
        fingerprint, never for the password. It is not stored anywhere by
        this integration -- only the returned token is.
        """

        latitude, longitude = coordinates
        payload = {
            # "mail"/"passwd" exactly: the server silently ignores "email",
            # "username" and "account", then answers with a generic
            # credential mismatch that reads as a wrong password.
            "mail": email,
            "passwd": password,
            # Any fingerprint-shaped string; never checked against anything.
            "imei": uuid.uuid4().hex,
            "lang": "en",
            "platform": "android",
            "lat": str(latitude),
            "lng": str(longitude),
            "package_name": PACKAGE_NAME,
            "package_version": PACKAGE_VERSION,
            "user_term_version": USER_TERM_VERSION,
            "user_privacy_version": USER_PRIVACY_VERSION,
        }

        response = self._request(LOGIN_PATH, form=payload)
        self._raise_for_ret(response, context="login")

        token = (response.get("info") or {}).get("token")
        if not token:
            raise AuthError("CozyLife accepted the login but returned no session token")
        return str(token)

    def devices(self, token: str) -> list[CozyLifeDevice]:
        """List every device on the account, bound and shared."""

        response = self._request(
            DEVICE_LIST_PATH,
            params={"token": token, "platform": "android", "lang": "en"},
        )
        self._raise_for_ret(response, context="device list")

        info = response.get("info") or {}
        devices: list[CozyLifeDevice] = []
        # The live wire format nests under readable keys one level deeper
        # than the app's own @JSONField annotations ("bindDevices") suggest.
        for section in ("device_bind", "device_share"):
            for raw in (info.get(section) or {}).get("list") or []:
                device = self._parse_device(raw)
                if device is not None:
                    devices.append(device)
        return devices

    def _parse_device(self, raw: dict[str, Any]) -> CozyLifeDevice | None:
        """Build a device, or skip it if it cannot be controlled."""

        device_id = raw.get("device_id")
        device_key = raw.get("device_key")
        if not device_id or not device_key:
            # Without a key there is nothing the relay transport could do
            # with this entry, so it is not worth surfacing to the user.
            _LOGGER.debug("Skipping device entry without a key: %s", device_id)
            return None

        dpids = raw.get("dpid")
        return CozyLifeDevice(
            device_id=str(device_id),
            device_key=str(device_key),
            name=str(raw.get("device_name") or device_id),
            # Relay endpoint is per-device. A device registered on one port
            # will accept a publish on another and deliver it to nobody.
            relay_host=str(raw.get("ip") or ""),
            relay_port=int(raw.get("port") or 0),
            local_ip=raw.get("local_ip") or None,
            model=raw.get("device_model_name") or None,
            dpids=tuple(int(d) for d in dpids if isinstance(d, int)) if isinstance(dpids, list) else (),
            online=bool(raw.get("is_online", 1)),
        )

    def _request(
        self,
        path: str,
        *,
        form: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform one API call and return the decoded body."""

        url = self._base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            # A 5xx means the request reached CozyLife and their code fell
            # over on it -- a malformed request, not an unreachable network.
            # Saying "cannot connect" would send the user to their router.
            if err.code >= 500:
                raise CozyLifeError(
                    f"CozyLife rejected the request with HTTP {err.code}. "
                    "Their server errors rather than validating when a login "
                    "field is missing or a value is one it cannot handle."
                ) from err
            raise CozyLifeError(
                f"CozyLife returned HTTP {err.code} for {path}"
            ) from err
        except urllib.error.URLError as err:
            raise CozyLifeError(f"cannot reach CozyLife at {path}: {err.reason}") from err

        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as err:
            raise CozyLifeError(f"CozyLife returned a non-JSON body for {path}") from err

        if not isinstance(decoded, dict):
            raise CozyLifeError(f"CozyLife returned an unexpected body for {path}")
        return decoded

    @staticmethod
    def _raise_for_ret(response: dict[str, Any], *, context: str) -> None:
        """Translate the API's ``ret`` code into an exception.

        Error text comes from the server's own ``desc``; the credentials
        that produced it are deliberately never interpolated into it.
        """

        ret = str(response.get("ret", ""))
        if ret == "1":
            return

        description = str(response.get("desc") or "").strip()
        if ret == "1001":
            raise AuthError(f"CozyLife rejected the password ({description})")
        if ret == "2006":
            raise AuthError(f"No account exists for that email address ({description})")
        raise AuthError(f"CozyLife {context} failed [{ret}] {description}")
