"""What was pasted into the token field, before Fyers is asked."""
import base64
import json
from datetime import UTC, datetime, timedelta

from macd_trader.brokers import describe_fyers_token


def jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc(claims)}.sig"


def test_an_auth_code_and_the_redirect_address_are_recognised():
    code = jwt({"iss": "api.login.fyers.in", "sub": "auth_code", "app_id": "XV2TQDA5K4", "exp": 2_000_000_000})
    assert describe_fyers_token(code)["kind"] == "auth_code"
    url = f"https://trade.fyers.in/api-login/redirect-uri/index.html?s=ok&code=200&auth_code={code}&state=None"
    seen = describe_fyers_token(url)
    assert seen["kind"] == "auth_code" and seen["value"] == code and seen["app_id"] == "XV2TQDA5K4"
    assert describe_fyers_token("https://trade.fyers.in/api-login/redirect-uri/index.html?s=error") == {
        "kind": "redirect", "value": None, "expires_at": None, "app_id": None}


def test_an_access_token_is_dated_and_garbage_is_left_to_fyers():
    expiry = datetime(2026, 10, 1, 0, 30, tzinfo=UTC)
    token = jwt({"iss": "api.fyers.in", "sub": "access_token", "exp": expiry.timestamp()})
    seen = describe_fyers_token(f"  {token} ")
    assert seen["kind"] == "access_token" and seen["expires_at"] == expiry and seen["value"] == token
    assert describe_fyers_token("not-a-token")["kind"] == "unknown"


def test_a_redirect_status_code_is_never_taken_for_the_auth_code():
    seen = describe_fyers_token("https://trade.fyers.in/api-login/redirect-uri/index.html?s=error&code=-1")
    assert seen["kind"] == "redirect" and seen["value"] is None
