"""Read-only Upstox historical-data access via Nomad Curie's saved credentials.

Tokens remain in process memory, are sent only to api.upstox.com, and are never
printed or persisted. Does not import/start Nomad's app or touch order routes.
"""
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request


def load_nomad_token():
    code = '''import os,json
from pathlib import Path
from core.security import decrypt_token
p=Path(os.environ.get("CREDENTIALS_FILE", "/app/credentials.json"))
raw=json.loads(p.read_text()); raw=raw.get("data",raw)
creds=raw.get("upstox",{})
field="analytics_token" if creds.get("analytics_token") else "access_token"
value=str(creds.get(field, "")).strip()
token=decrypt_token(value[len("fernet::"):]) if value.startswith("fernet::") else value
print(json.dumps({"token":token,"source":field}))
'''
    result = subprocess.run(["docker", "exec", "nomadcurie_backend", "python", "-c", code],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError("Nomad Curie credential read failed; details suppressed to protect secrets")
    payload = json.loads(result.stdout)
    if not payload.get("token"):
        raise RuntimeError("No saved Upstox data token")
    return payload["token"], payload["source"]


def get_data(token, path, params=None):
    if not path.startswith(("/expired-instruments/", "/historical-candle/", "/instruments/")):
        raise ValueError("Only historical/instrument GET routes are allowed")
    url = "https://api.upstox.com/v2" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token,
                                                   "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def probe():
    token, source = load_nomad_token()
    status, payload = get_data(token, "/expired-instruments/option/contract", {
        "instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": "2026-08-25"})
    result = {"credential_source": "Nomad Curie " + source, "http": status,
              "status": payload.get("status"), "contracts": len(payload.get("data", [])),
              "message": payload.get("message"), "error": payload.get("error"),
              "detail": payload.get("detail"), "error_code": payload.get("error_code"),
              "retryable": payload.get("retryable"), "owner_action_required": payload.get("owner_action_required"),
              "errors": [{"code": row.get("errorCode"), "message": row.get("message")}
                         for row in payload.get("errors", [])]}
    if status == 200 and payload.get("data"):
        contracts = payload["data"]
        selected = min((c for c in contracts if c["instrument_type"] == "CE"),
                       key=lambda c: abs(c["strike_price"] - 24300))
        key = urllib.parse.quote(selected["instrument_key"], safe="")
        candle_status, candles = get_data(token, f"/expired-instruments/historical-candle/{key}/1minute/2026-08-25/2026-08-03")
        rows = candles.get("data", {}).get("candles", [])
        result["history_probe"] = {"http": candle_status, "symbol": selected["trading_symbol"],
            "instrument_key": selected["instrument_key"], "rows": len(rows),
            "first": rows[-1][0] if rows else None, "last": rows[0][0] if rows else None,
            "errors": [{"code": row.get("errorCode"), "message": row.get("message")}
                       for row in candles.get("errors", [])]}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    probe()
