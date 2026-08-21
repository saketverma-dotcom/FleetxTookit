"""Messaging tab logic (v3.5) — pure, testable pieces for the two-way SMS
console. No Tk, no live network (callers inject a session/requests).

Realtime is polling: SemySMS has no push to a firewalled desktop, so we call
inbox_sms.php every few seconds, using the highest id we've seen as a high-water
mark so each poll only surfaces genuinely new messages.
"""
from .config import SEMYSMS_API, SEMYSMS_INBOX_API, SEMYSMS_OUTBOX_API


# ─────────────── request builders ───────────────

def build_inbox_request(token, device_id, since_id=None):
    """(url, params) for inbox_sms.php for one device. If since_id is set we ask
    only for ids after it (start_id); else today's messages (API default)."""
    params = {"token": token, "device": str(device_id)}
    if since_id:
        params["start_id"] = str(int(since_id) + 1)
    return SEMYSMS_INBOX_API, params


def build_reply_request(token, device_id, phone, msg):
    """(url, data) for sending a reply via sms.php from a specific device."""
    return SEMYSMS_API, {"token": token, "device": str(device_id),
                         "phone": phone, "msg": msg}


# ─────────────── response parsing ───────────────

def parse_inbox(resp_json):
    """Normalize inbox_sms.php output to a list of dicts:
    {id:int, phone:str, msg:str, date:str}. Tolerates missing/って bad rows."""
    if not isinstance(resp_json, dict):
        return []
    out = []
    for row in resp_json.get("data") or []:
        try:
            out.append({
                "id": int(row.get("id")),
                "phone": str(row.get("phone", "")).strip(),
                "msg": str(row.get("msg", "")),
                "date": str(row.get("date", "")),
                "device": str(row.get("id_device", "")),
            })
        except (TypeError, ValueError):
            continue
    return out


def max_id(messages, current=0):
    """Highest message id across `messages`, not below `current`. Used as the
    poll high-water mark so we never re-fetch or re-notify old messages."""
    hi = int(current or 0)
    for m in messages:
        if m.get("id", 0) > hi:
            hi = m["id"]
    return hi


def new_since(messages, last_seen_id):
    """Subset of messages strictly newer than last_seen_id, oldest-first."""
    fresh = [m for m in messages if m.get("id", 0) > int(last_seen_id or 0)]
    return sorted(fresh, key=lambda m: m["id"])


# ─────────────── conversation model ───────────────

def add_messages(threads, messages, direction="in"):
    """Merge `messages` into a threads dict keyed by phone number.
    threads: { phone: [ {id, phone, msg, date, dir}, ... ] }.
    Dedupes incoming by id; outgoing (no server id yet) appended as-is.
    Returns the same dict (mutated) for convenience."""
    for m in messages:
        phone = m.get("phone", "").strip()
        if not phone:
            continue
        entry = dict(m)
        entry["dir"] = direction
        bucket = threads.setdefault(phone, [])
        if direction == "in":
            if any(e.get("dir") == "in" and e.get("id") == entry.get("id")
                   for e in bucket):
                continue      # already have this incoming id
        bucket.append(entry)
    return threads


def thread_order(threads):
    """Phone numbers ordered by most-recent activity first (by latest
    timestamp, falling back to id). Drives the conversation list."""
    def key(phone):
        msgs = threads[phone]
        return max((_ts_key(m) for m in msgs), default=("", 0))
    return sorted(threads.keys(), key=key, reverse=True)


def conversation(threads, phone):
    """Ordered message list for one number, sorted by real timestamp so
    incoming and outgoing interleave in true chronological order."""
    return sorted(threads.get(phone, []), key=_ts_key)


# ─────────────── outbox / delivery status ───────────────

SENT_PENDING, SENT_SENT, SENT_DELIVERED = "pending", "sent", "delivered"
SENT_FAILED, SENT_CANCELLED = "failed", "cancelled"

STATUS_LABEL = {
    SENT_PENDING:   "\u23f3 Pending",
    SENT_SENT:      "\u2713 Sent",
    SENT_DELIVERED: "\u2713\u2713 Delivered",
    SENT_FAILED:    "\u2717 Failed",
    SENT_CANCELLED: "\u2298 Cancelled",
}

TERMINAL_STATUSES = {SENT_DELIVERED, SENT_FAILED, SENT_CANCELLED}


def build_outbox_request(token, device_id, list_id):
    """(url, params) for outbox_sms.php filtered to specific SMS ids."""
    params = {"token": token, "device": str(device_id)}
    if list_id:
        params["list_id"] = ",".join(str(i) for i in list_id)
    return SEMYSMS_OUTBOX_API, params


def status_from_row(row):
    """Map an outbox row's flags to one of the SENT_* states."""
    def flag(k):
        try:
            return int(row.get(k, 0) or 0) == 1
        except (TypeError, ValueError):
            return False
    if flag("is_error") or flag("is_error_send"):
        return SENT_FAILED
    if flag("is_cancel"):
        return SENT_CANCELLED
    if flag("is_delivered"):
        return SENT_DELIVERED
    if flag("is_send"):
        return SENT_SENT
    return SENT_PENDING


def parse_outbox_status(resp_json):
    """{sms_id(int): status} from an outbox_sms.php response."""
    out = {}
    if not isinstance(resp_json, dict):
        return out
    for row in resp_json.get("data") or []:
        try:
            out[int(row.get("id"))] = status_from_row(row)
        except (TypeError, ValueError):
            continue
    return out


def _ts_key(m):
    """Sort key: real timestamp string if present, else empty (sorts first).
    ISO-ish 'YYYY-MM-DD HH:MM:SS...' strings sort correctly lexicographically."""
    return (m.get("date") or "", m.get("id", 0))


# ─────────────── message length / segment helper ───────────────

def sms_segments(text):
    """(char_count, segment_count) for an SMS. GSM: 160 chars/segment single,
    153/segment when concatenated. Unicode (non-GSM chars): 70 / 67. Rough but
    matches how SemySMS/carriers bill and split."""
    text = text or ""
    n = len(text)
    # crude GSM-7 detection: if any char is outside basic latin/common set,
    # treat the whole message as Unicode (UCS-2).
    gsm = all(ord(c) < 128 for c in text)
    if n == 0:
        return 0, 0
    if gsm:
        return (n, 1) if n <= 160 else (n, -(-n // 153))
    return (n, 1) if n <= 70 else (n, -(-n // 67))


# ─────────────── UI helpers (avatar color, initials, filtering) ───────────────

# Pulse-style flat palette for avatar circles
AVATAR_COLORS = ["#e91e63", "#ff9800", "#9e9e9e", "#673ab7", "#00bcd4",
                 "#ffc107", "#795548", "#4caf50", "#3f51b5", "#f44336",
                 "#009688", "#8bc34a"]


def avatar_color(phone):
    """Deterministic color for a number, so each contact keeps one hue."""
    digits = "".join(ch for ch in str(phone) if ch.isdigit()) or "0"
    return AVATAR_COLORS[int(digits[-4:] or 0) % len(AVATAR_COLORS)]


def avatar_initials(phone):
    """Last two digits of the number — a compact, stable avatar label."""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return digits[-2:] if len(digits) >= 2 else (digits or "?")


def preview_text(threads, phone, limit=38):
    """Last message body for the conversation-row preview."""
    msgs = threads.get(phone, [])
    if not msgs:
        return "(new conversation)"
    last = sorted(msgs, key=_ts_key)[-1]
    body = (last.get("msg") or "").replace("\n", " ").strip()
    return body[:limit] + ("…" if len(body) > limit else "")


def last_time(threads, phone):
    """Short HH:MM of the latest message in a thread, for the row timestamp."""
    msgs = threads.get(phone, [])
    if not msgs:
        return ""
    date = sorted(msgs, key=_ts_key)[-1].get("date", "")
    if " " in date:
        parts = date.split(" ", 1)[1].split(":")
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}"
    return ""


def filter_threads(threads, query="", unread=None):
    """Ordered phone list filtered by a search query (matches number or any
    message text) and/or unread flag. `unread` is a set of phones with unread
    incoming; if given, only those are returned."""
    order = thread_order(threads)
    q = (query or "").strip().lower()
    out = []
    for phone in order:
        if unread is not None and phone not in unread:
            continue
        if q:
            hay = phone.lower() + " " + " ".join(
                (m.get("msg") or "").lower() for m in threads[phone])
            if q not in hay:
                continue
        out.append(phone)
    return out


# ─────────────── adaptive poll interval (v3.10 performance) ───────────────

POLL_BASE      = 7      # seconds, when a conversation is active
POLL_MAX       = 30     # never wait longer than this
IDLE_STEPS     = (7, 15, 30)   # ladder as idle cycles accumulate


def next_poll_interval(idle_cycles):
    """Seconds to wait before the next poll, given how many consecutive polls
    returned nothing new. Keeps 7s while active, stretches to 30s when quiet —
    cutting API load a lot without hurting responsiveness in a live chat."""
    if idle_cycles <= 0:
        return IDLE_STEPS[0]
    if idle_cycles < 5:
        return IDLE_STEPS[0]
    if idle_cycles < 15:
        return IDLE_STEPS[1]
    return IDLE_STEPS[2]


# ─────────────── human-readable network errors ───────────────

def friendly_error(exc):
    """Turn a requests/urllib exception into something a fleet ops user can act
    on. Raw text like "HTTPSConnectionPool(host='semysms.net', port=443): Read
    timed out" tells them nothing useful."""
    name = type(exc).__name__
    text = str(exc).lower()
    if "read timed out" in text or "readtimeout" in name.lower():
        return "SemySMS is slow to respond — still retrying"
    if "timed out" in text or "timeout" in name.lower():
        return "SemySMS timed out — still retrying"
    if ("nameresolution" in text or "getaddrinfo" in text
            or "temporary failure in name resolution" in text):
        return "No internet connection — still retrying"
    if "connection" in text or "connect" in name.lower():
        return "Can't reach SemySMS — check your connection"
    if "json" in text or "expecting value" in text:
        return "SemySMS returned an unexpected response — still retrying"
    return "SemySMS error — still retrying"
