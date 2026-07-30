"""Tests for Google One-Tap SSO logic (v3.4). No browser, no network.
Uses the real captured Google ID token (signature stripped) for decode tests."""
import time

import pytest

from fleetx_toolkit import google_auth as ga

# Real captured Google ID token payload (signature replaced with 'SIG').
REAL_JWT = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6IjMwZmUwZTIzYzRkNmUzNmM1MjU3N2IxZTJmZWZkMWFiYzM4"
    "ODk1ZGUiLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJodHRwczovL2FjY291bnRzLmdvb2dsZS5jb20iLC"
    "JhenAiOiIxMzUyNjQ3NjQwNjMtaGNzOHJpMW1tN3A3YnEwcDY5a3J2bG50bHBrY3E0cmUuYXBwcy5n"
    "b29nbGV1c2VyY29udGVudC5jb20iLCJhdWQiOiIxMzUyNjQ3NjQwNjMtaGNzOHJpMW1tN3A3YnEwcD"
    "Y5a3J2bG50bHBrY3E0cmUuYXBwcy5nb29nbGV1c2VyY29udGVudC5jb20iLCJzdWIiOiIxMDc1MzQw"
    "ODE0MDU5MjY2NTI5MDYiLCJoZCI6ImZsZWV0eC5pbyIsImVtYWlsIjoic2FrZXQudmVybWFAZmxlZX"
    "R4LmlvIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsIm5iZiI6MTc4NTM4OTU5MCwibmFtZSI6IlNha2V0"
    "IEt1bWFyIFZlcm1hIiwicGljdHVyZSI6Imh0dHBzOi8vbGgzLmdvb2dsZXVzZXJjb250ZW50LmNvbS"
    "9hL0FDZzhvY0kzajNxR3N5RFBHRWRFMjRCVXQtbnBpQlVPLVJoTE5jT1dfVGY0UTNsM0gxNGxNZz1z"
    "OTYtYyIsImdpdmVuX25hbWUiOiJTYWtldCIsImZhbWlseV9uYW1lIjoiS3VtYXIgVmVybWEiLCJpYX"
    "QiOjE3ODUzODk4OTAsImV4cCI6MTc4NTM5MzQ5MCwianRpIjoiYzhiYzJkZjE4YWQxMWNkOTM0MmFl"
    "NmVlMTVkZTgyZGIxYTBjYzBlNiJ9.SIG"
)


@pytest.fixture
def claims():
    return ga.decode_id_token(REAL_JWT)


@pytest.fixture
def valid(claims):
    # same claims but guaranteed unexpired
    c = dict(claims); c["exp"] = time.time() + 3600
    return c


class TestDecode:
    def test_reads_email_and_domain(self, claims):
        assert claims["email"] == "saket.verma@fleetx.io"
        assert claims["hd"] == "fleetx.io"
        assert claims["aud"] == ga.GOOGLE_CLIENT_ID

    def test_google_email_lowercased(self, claims):
        assert ga.google_email(claims) == "saket.verma@fleetx.io"

    def test_garbage_returns_empty(self):
        assert ga.decode_id_token("not.a.jwt") == {}
        assert ga.decode_id_token("") == {}


class TestValidateClaims:
    def test_valid(self, valid):
        ok, reason = ga.validate_claims(valid)
        assert ok and reason == ""

    def test_wrong_domain_rejected(self, valid):
        ok, reason = ga.validate_claims(dict(valid, hd="gmail.com"))
        assert not ok and "fleetx.io" in reason

    def test_wrong_audience_rejected(self, valid):
        ok, reason = ga.validate_claims(dict(valid, aud="someone-else"))
        assert not ok and "not issued for FleetX" in reason

    def test_unverified_email_rejected(self, valid):
        ok, _ = ga.validate_claims(dict(valid, email_verified=False))
        assert not ok

    def test_expired_rejected(self, valid):
        ok, reason = ga.validate_claims(dict(valid, exp=1))
        assert not ok and "expired" in reason

    def test_empty_rejected(self):
        assert not ga.validate_claims({})[0]


class TestExchange:
    def test_request_shape(self):
        url, files, headers = ga.build_exchange_request("ID_TOK")
        assert url.endswith("/api/v2/login/google-one-tap")
        assert files["token"] == (None, "ID_TOK")
        assert headers["clientid"] == "fleetxweb"

    @pytest.mark.parametrize("body,expected", [
        ({"access_token": "B1"}, "B1"),
        ({"token": "B2"}, "B2"),
        ({"data": {"token": "B3"}}, "B3"),
        ({"result": {"access_token": "B4"}}, "B4"),
        ({"nope": 1}, None),
    ])
    def test_extract_bearer(self, body, expected):
        assert ga.extract_bearer(body) == expected

    class _Resp:
        def __init__(self, code, js=None, text=""):
            self.status_code = code; self._js = js; self.text = text
        def json(self):
            if self._js is None:
                raise ValueError("no json")
            return self._js

    class _Sess:
        def __init__(self, resp): self.resp = resp
        def post(self, *a, **k): return self.resp

    def test_success(self):
        tok, err = ga.exchange_google_token(
            "x", session=self._Sess(self._Resp(200, {"access_token": "BEARER"})))
        assert tok == "BEARER" and err == ""

    def test_cloudflare_block(self):
        tok, err = ga.exchange_google_token(
            "x", session=self._Sess(self._Resp(403, {}, "Cloudflare Attention Required")))
        assert tok is None and "Cloudflare" in err

    def test_200_but_no_token(self):
        tok, err = ga.exchange_google_token(
            "x", session=self._Sess(self._Resp(200, {"z": 1})))
        assert tok is None and "no token" in err

    def test_http_error(self):
        tok, err = ga.exchange_google_token(
            "x", session=self._Sess(self._Resp(401, {"message": "bad creds"})))
        assert tok is None and "HTTP 401" in err

    def test_network_exception(self):
        class Boom:
            def post(self, *a, **k): raise ConnectionError("down")
        tok, err = ga.exchange_google_token("x", session=Boom())
        assert tok is None and "Network error" in err
