"""HTTP header construction for FleetX API calls."""
from .config import APP_BASE


def api_headers(token, form=False, content_type=None):
    h = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en",
        "authorization": f"Bearer {token}",
        "clientid": "fleetxweb",
        "origin": APP_BASE,
        "referer": f"{APP_BASE}/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }
    if content_type:
        h["content-type"] = content_type
    elif form:
        h["content-type"] = "application/x-www-form-urlencoded"
    else:
        h["content-type"] = "application/json;charset=UTF-8"
    return h
