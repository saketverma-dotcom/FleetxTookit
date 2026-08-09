"""Messaging tab logic (v3.5) — pure, testable pieces for the two-way SMS
console. No Tk, no live network (callers inject a session/requests).

Realtime is polling: SemySMS has no push to a firewalled desktop, so we call
inbox_sms.php every few seconds, using the highest id we've seen as a high-water
mark so each poll only surfaces genuinely new messages.
"""
from .config import SEMYSMS_API, SEMYSMS_INBOX_API


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
    """Phone numbers ordered by most-recent activity first (by max id seen,
    falling back to insertion). Drives the conversation list."""
    def key(phone):
        msgs = threads[phone]
        return max((m.get("id", 0) for m in msgs), default=0)
    return sorted(threads.keys(), key=key, reverse=True)


def conversation(threads, phone):
    """Ordered message list for one number: incoming by id, outgoing appended
    in send order, interleaved by best-effort (id then arrival)."""
    msgs = threads.get(phone, [])
    # incoming have real ids; outgoing may have id=0 -> keep stable order
    return sorted(msgs, key=lambda m: (m.get("id", 0) == 0, m.get("id", 0)))
