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
