"""CozyLife's cloud HTTP API: log in, list the account's devices.

The integration needs this for exactly one thing -- discovering each
device's ``device_key`` and its assigned relay host/port, which are the
credentials the relay transport needs and which cannot be discovered any
other way.

Most assertions below pin down a detail that is non-obvious and was
established by decompiling the vendor's own app. Each one is a real
gotcha: get it wrong and the request fails in a way that looks like bad
credentials rather than a malformed request.
"""

import pytest

from custom_components.cozylife_cloud.api.account import CozyLifeAccount
from custom_components.cozylife_cloud.api.errors import AuthError, CozyLifeError

from .fakes import FakeHttpServer

LOGIN_PATH = "/api/app/user/login"
DEVICES_PATH = "/api/v2/app/device_with_group/list"

TOKEN = "fake-session-token"

DEVICE_LIST_BODY = {
    "ret": "1",
    "desc": "Success",
    "info": {
        "device_bind": {
            "count": 1,
            "list": [
                {
                    "device_id": "aaaabbbbccccdddd0001",
                    "device_key": "fake-key-one",
                    "device_name": "Test Socket",
                    "ip": "203.0.113.10",
                    "port": 8898,
                    "dpid": [1, 26, 28],
                    "is_online": 1,
                    "device_model_name": "Metering Socket",
                }
            ],
        },
        "device_share": {"count": 0, "list": []},
        "device_group_bind": {"count": 0, "list": []},
        "device_group_share": {"count": 0, "list": []},
    },
}


def ok_login(token=TOKEN):
    return {"ret": "1", "desc": "Success", "info": {"token": token, "uid": "1"}}


def account(server) -> CozyLifeAccount:
    return CozyLifeAccount(base_url=server.base_url, timeout=5)


# --- login -----------------------------------------------------------------


def test_login_returns_the_session_token():
    with FakeHttpServer({LOGIN_PATH: ok_login()}) as server:
        assert account(server).login("someone@example.com", "hunter2") == TOKEN


def test_login_posts_the_credentials_form_encoded():
    with FakeHttpServer({LOGIN_PATH: ok_login()}) as server:
        account(server).login("someone@example.com", "hunter2")

        request = server.requests[0]
        assert request["path"] == LOGIN_PATH
        assert "application/x-www-form-urlencoded" in request["headers"]["Content-Type"]


def test_login_names_the_fields_mail_and_passwd():
    """Not ``email``/``username``/``account``: the server silently ignores
    any other spelling and answers with a generic credential mismatch, which
    looks exactly like a wrong password."""

    with FakeHttpServer({LOGIN_PATH: ok_login()}) as server:
        account(server).login("someone@example.com", "hunter2")

        form = server.requests[0]["form"]
        assert form["mail"] == "someone@example.com"
        assert form["passwd"] == "hunter2"


def test_login_sends_coordinates_that_are_not_null_island():
    """On a *successful* login the server does a country lookup to pick a
    relay, and throws an unhandled 500 when the coordinates resolve to no
    country. 0,0 is the one value that must never be sent. A failed login
    never reaches that code, so this only ever breaks in production."""

    with FakeHttpServer({LOGIN_PATH: ok_login()}) as server:
        account(server).login("someone@example.com", "hunter2")

        form = server.requests[0]["form"]
        assert (float(form["lat"]), float(form["lng"])) != (0.0, 0.0)


def test_login_sends_a_user_agent_cloudflare_will_accept():
    """Cloudflare fronts this API and blocks Python's default
    ``Python-urllib/3.x`` outright with a 403 before CozyLife ever sees the
    request -- a bot filter that is easily mistaken for an account lockout."""

    with FakeHttpServer({LOGIN_PATH: ok_login()}) as server:
        account(server).login("someone@example.com", "hunter2")

        user_agent = server.requests[0]["headers"]["User-Agent"]
        assert "urllib" not in user_agent.lower()
        assert user_agent


def test_login_sends_the_terms_versions_the_server_requires():
    with FakeHttpServer({LOGIN_PATH: ok_login()}) as server:
        account(server).login("someone@example.com", "hunter2")

        form = server.requests[0]["form"]
        assert form["user_term_version"]
        assert form["user_privacy_version"]
        assert form["package_name"]


def test_a_wrong_password_raises_a_recognisable_auth_error():
    body = {"ret": "1001", "desc": "account or password error"}
    with FakeHttpServer({LOGIN_PATH: body}) as server, pytest.raises(
        AuthError, match="password"
    ):
        account(server).login("someone@example.com", "wrong")


def test_an_unknown_account_raises_a_recognisable_auth_error():
    body = {"ret": "2006", "desc": "user not exist"}
    with FakeHttpServer({LOGIN_PATH: body}) as server, pytest.raises(
        AuthError, match=r"[Nn]o account"
    ):
        account(server).login("nobody@example.com", "hunter2")


def test_a_login_that_succeeds_without_a_token_is_still_an_error():
    body = {"ret": "1", "desc": "Success", "info": {}}
    with FakeHttpServer({LOGIN_PATH: body}) as server, pytest.raises(AuthError):
        account(server).login("someone@example.com", "hunter2")


def test_the_password_is_never_included_in_an_error_message():
    body = {"ret": "1001", "desc": "account or password error"}
    with FakeHttpServer({LOGIN_PATH: body}) as server:
        with pytest.raises(AuthError) as caught:
            account(server).login("someone@example.com", "s3cr3t-pw")

        assert "s3cr3t-pw" not in str(caught.value)


# --- device list -----------------------------------------------------------


def test_devices_authenticates_with_a_token_request_parameter():
    """The app never uses bearer auth. Every authenticated call carries the
    token as an ordinary request parameter; an Authorization header is
    ignored, which sends you chasing an OAuth flow that does not exist."""

    with FakeHttpServer({DEVICES_PATH: DEVICE_LIST_BODY}) as server:
        account(server).devices(TOKEN)

        request = server.requests[0]
        assert request["query"]["token"] == TOKEN
        assert "Authorization" not in request["headers"]


def test_devices_parses_the_real_response_shape():
    """The wire format nests under ``info.device_bind.list``, NOT the
    ``bindDevices`` the app's own annotations suggest."""

    with FakeHttpServer({DEVICES_PATH: DEVICE_LIST_BODY}) as server:
        devices = account(server).devices(TOKEN)

        assert len(devices) == 1
        assert devices[0].device_id == "aaaabbbbccccdddd0001"
        assert devices[0].device_key == "fake-key-one"
        assert devices[0].name == "Test Socket"


def test_devices_reads_the_relay_host_and_port_per_device():
    """Relay port is per-device, never a constant. A device registered on
    one port will accept a publish on another and deliver it to nobody."""

    with FakeHttpServer({DEVICES_PATH: DEVICE_LIST_BODY}) as server:
        device = account(server).devices(TOKEN)[0]

        assert device.relay_host == "203.0.113.10"
        assert device.relay_port == 8898


def test_devices_includes_devices_shared_with_the_account():
    body = {
        "ret": "1",
        "info": {
            "device_bind": {"list": []},
            "device_share": {
                "list": [
                    {
                        "device_id": "aaaabbbbccccdddd0002",
                        "device_key": "fake-key-two",
                        "device_name": "Shared Socket",
                        "ip": "203.0.113.11",
                        "port": 8899,
                    }
                ]
            },
        },
    }
    with FakeHttpServer({DEVICES_PATH: body}) as server:
        devices = account(server).devices(TOKEN)

        assert [d.device_id for d in devices] == ["aaaabbbbccccdddd0002"]


def test_devices_survives_an_account_with_nothing_on_it():
    body = {"ret": "1", "info": {"device_bind": {"list": []}, "device_share": {"list": []}}}
    with FakeHttpServer({DEVICES_PATH: body}) as server:
        assert account(server).devices(TOKEN) == []


def test_an_expired_token_raises_auth_error_so_the_caller_can_re_login():
    body = {"ret": "1005", "desc": "token invalid"}
    with FakeHttpServer({DEVICES_PATH: body}) as server, pytest.raises(AuthError):
        account(server).devices("stale-token")


def test_a_device_entry_missing_its_key_is_skipped_rather_than_crashing():
    body = {
        "ret": "1",
        "info": {
            "device_bind": {
                "list": [
                    {"device_id": "aaaabbbbccccdddd0003", "device_name": "Keyless"},
                    DEVICE_LIST_BODY["info"]["device_bind"]["list"][0],
                ]
            }
        },
    }
    with FakeHttpServer({DEVICES_PATH: body}) as server:
        devices = account(server).devices(TOKEN)

        assert [d.device_id for d in devices] == ["aaaabbbbccccdddd0001"]


def test_a_device_repr_does_not_leak_its_key():
    with FakeHttpServer({DEVICES_PATH: DEVICE_LIST_BODY}) as server:
        device = account(server).devices(TOKEN)[0]

        assert "fake-key-one" not in repr(device)


def test_login_sends_the_package_version():
    """Omitting this makes a *correct* login fail with an unhandled HTTP 500.

    The server's own required-field list does not mention it, so probing
    with a deliberately wrong password never reveals the problem -- the
    failure is in the success path, past validation. Exactly the same trap
    as the Null Island coordinates above, and it cost a real login attempt
    to find both times.
    """

    with FakeHttpServer({LOGIN_PATH: ok_login()}) as server:
        account(server).login("someone@example.com", "hunter2")

        assert server.requests[0]["form"]["package_version"]


def test_a_server_error_on_login_is_reported_as_a_login_problem():
    """A 500 here means the request reached CozyLife and their code fell
    over on it -- a malformed request, not an unreachable network. Reporting
    it as 'cannot connect' sends the user to check their router."""

    import http.server
    import threading

    class Boom(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Boom)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        host, port = server.server_address[:2]
        client = CozyLifeAccount(base_url=f"http://{host}:{port}", timeout=5)
        with pytest.raises(CozyLifeError) as caught:
            client.login("someone@example.com", "hunter2")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "500" in str(caught.value)
    assert "rejected" in str(caught.value).lower()
