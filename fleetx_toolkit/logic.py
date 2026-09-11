"""Pure business logic — no Tk, no network, no file I/O.
Extracted from _run_tickets and _do_one so it can be unit-tested; the UI
methods delegate here. Semantics are byte-for-byte those of v3.0."""

import random as _random

# ─────────────── retry backoff (429 + transient network errors) ───────────────

RETRY_BACKOFFS = [5, 15, 30]     # seconds after 1st/2nd/3rd retryable failure


def retry_wait(attempt, backoffs=None):
    """Seconds to wait before retry number `attempt` (0-based), or None when
    the ladder is exhausted and the item should be recorded as failed."""
    b = RETRY_BACKOFFS if backoffs is None else backoffs
    return b[attempt] if 0 <= attempt < len(b) else None


# ─────────────── ticket quota splitting ───────────────

def split_tickets_equal(tickets, assignee_ids, rng=None):
    """Even round-robin split across assignees, over a shuffled copy of the
    tickets (shuffling avoids always giving the first person the oldest IDs).
    `rng` is injectable for deterministic tests."""
    shuffled = list(tickets)
    (rng or _random).shuffle(shuffled)
    return [(t, assignee_ids[i % len(assignee_ids)])
            for i, t in enumerate(shuffled)]


def split_tickets_by_counts(tickets, chosen):
    """Explicit-counts split. `chosen` is an ordered list of
    (assignee_id, raw_count_string); blank or 'rest' means "share of the
    remainder". Order matters: fixed counts consume tickets front-to-back
    (top of the on-screen list is assigned first).

    Returns (assignments, unassigned_count, invalid):
      assignments      list of (ticket, assignee_id)
      unassigned_count tickets left over when counts < total and nobody is 'rest'
      invalid          None, or (position_in_chosen, raw) for a non-numeric count
    """
    assignments, idx, rest_targets = [], 0, []
    for pos, (aid, raw) in enumerate(chosen):
        raw = (raw or "").strip().lower()
        if raw in ("", "rest"):
            rest_targets.append(aid)
            continue
        try:
            n = int(raw)
        except ValueError:
            return [], 0, (pos, raw)
        for t in tickets[idx:idx + n]:
            assignments.append((t, aid))
        idx += n
    remaining = tickets[idx:]
    if remaining and rest_targets:
        for i, t in enumerate(remaining):
            assignments.append((t, rest_targets[i % len(rest_targets)]))
        remaining = []
    return assignments, len(remaining), None


# ─────────────── Asset assign / attach row parsing (v3.14) ───────────────
#
# Two flows share these parsers:
#   • paste  — one record per line, comma-separated
#   • Excel  — one record per row, by column name
# A default accountId (from the tab) fills in whenever a record omits it.

def _clean(v):
    """Trim a cell/field to a string; None and blanks become ''."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("none", "nan") else s


def parse_account_paste(text, default_account):
    """Lines of 'assetId' or 'assetId,accountId'.
    Returns (rows, errors); rows are dicts {asset_id, account_id}."""
    rows, errors = [], []
    for lineno, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = [_clean(p) for p in line.split(",")]
        asset = parts[0] if parts else ""
        acct = parts[1] if len(parts) > 1 and parts[1] else _clean(default_account)
        if not asset:
            errors.append(f"Line {lineno}: missing asset id.")
            continue
        if not acct:
            errors.append(f"Line {lineno}: no accountId (and no default set).")
            continue
        rows.append({"asset_id": asset, "account_id": acct})
    return rows, errors


def parse_account_records(records, default_account):
    """Excel rows with columns assetid / accountid (accountid optional)."""
    rows, errors = [], []
    for i, rec in enumerate(records or [], start=2):     # row 1 is the header
        asset = _clean(rec.get("assetid") or rec.get("asset_id") or rec.get("asset"))
        acct = _clean(rec.get("accountid") or rec.get("account_id")) or _clean(default_account)
        if not asset:
            errors.append(f"Row {i}: missing assetId.")
            continue
        if not acct:
            errors.append(f"Row {i}: no accountId (and no default set).")
            continue
        rows.append({"asset_id": asset, "account_id": acct})
    return rows, errors


def parse_attach_paste(text, default_account):
    """Lines of 'assetId,vehicleId' or 'assetId,vehicleId,accountId'."""
    rows, errors = [], []
    for lineno, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = [_clean(p) for p in line.split(",")]
        asset = parts[0] if parts else ""
        vehicle = parts[1] if len(parts) > 1 else ""
        acct = parts[2] if len(parts) > 2 and parts[2] else _clean(default_account)
        if not asset or not vehicle:
            errors.append(f"Line {lineno}: need assetId,vehicleId.")
            continue
        if not str(vehicle).isdigit():
            errors.append(f"Line {lineno}: vehicleId '{vehicle}' must be numeric.")
            continue
        if not acct:
            errors.append(f"Line {lineno}: no accountId (and no default set).")
            continue
        rows.append({"asset_id": asset, "vehicle_id": int(vehicle),
                     "account_id": acct})
    return rows, errors


def parse_attach_records(records, default_account):
    """Excel rows with columns assetid / vehicleid / accountid (optional)."""
    rows, errors = [], []
    for i, rec in enumerate(records or [], start=2):
        asset = _clean(rec.get("assetid") or rec.get("asset_id") or rec.get("asset"))
        vehicle = _clean(rec.get("vehicleid") or rec.get("vehicle_id") or rec.get("vehicle"))
        acct = _clean(rec.get("accountid") or rec.get("account_id")) or _clean(default_account)
        if not asset or not vehicle:
            errors.append(f"Row {i}: need assetId and vehicleId.")
            continue
        if not vehicle.isdigit():
            errors.append(f"Row {i}: vehicleId '{vehicle}' must be numeric.")
            continue
        if not acct:
            errors.append(f"Row {i}: no accountId (and no default set).")
            continue
        rows.append({"asset_id": asset, "vehicle_id": int(vehicle),
                     "account_id": acct})
    return rows, errors


def build_account_assign(row):
    """(params, json_body) for PUT /assets/account — body is an ARRAY, and we
    send exactly one asset per call so each row reports its own result."""
    return {"accountId": str(row["account_id"])}, [str(row["asset_id"])]


def build_attach_body(row, asset_type):
    """JSON body for POST /assets/attach."""
    return {"assetId": str(row["asset_id"]),
            "vehicleId": int(row["vehicle_id"]),
            "type": asset_type,
            "accountId": int(row["account_id"]) if str(row["account_id"]).isdigit()
                         else row["account_id"]}


# ─────────────── Bulk Onboard: 5-step sequential flow (v3.15) ───────────────
#
# Per row, in order:
#   1 Device Add            POST /api/v1/devices/
#   2 Asset Add             POST /api/v1/assets
#   3 Vehicle-Device Map    POST /api/v1/vehicles/device
#   4 Asset -> Account      PUT  /api/v1/assets/account?accountId=
#   5 Asset -> Vehicle      POST /api/v1/assets/attach
# assetId == deviceId (the IMEI), so it is not a separate column.
# A failing step stops that row; remaining steps are marked SKIPPED.

ONBOARD_STEPS = ["Device Add", "Asset Add", "Vehicle Map",
                 "Asset→Account", "Asset→Vehicle"]

ONBOARD_COLUMNS = ["deviceid", "vehicleid", "accountid", "sim",
                   "serialnumber", "devicetype"]


def parse_onboard_paste(text, defaults):
    """Lines of: deviceId, vehicleId, accountId [, sim [, serialNumber [, deviceType]]]
    Returns (rows, errors)."""
    rows, errors = [], []
    for lineno, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        p = [_clean(x) for x in line.split(",")]
        rec = {
            "deviceid": p[0] if len(p) > 0 else "",
            "vehicleid": p[1] if len(p) > 1 else "",
            "accountid": p[2] if len(p) > 2 else "",
            "sim": p[3] if len(p) > 3 else "",
            "serialnumber": p[4] if len(p) > 4 else "",
            "devicetype": p[5] if len(p) > 5 else "",
        }
        row, err = _onboard_row(rec, defaults, f"Line {lineno}")
        (rows if row else errors).append(row or err)
    return rows, errors


def parse_onboard_records(records, defaults):
    """Excel rows keyed by column name (see ONBOARD_COLUMNS)."""
    rows, errors = [], []
    for i, rec in enumerate(records or [], start=2):
        norm = {}
        for k, v in (rec or {}).items():
            key = str(k or "").strip().lower().replace("_", "")
            norm[key] = v
        row, err = _onboard_row(norm, defaults, f"Row {i}")
        (rows if row else errors).append(row or err)
    return rows, errors


def _onboard_row(rec, defaults, where):
    """Validate one record. Returns (row, None) or (None, error-string)."""
    device = _clean(rec.get("deviceid") or rec.get("imei") or rec.get("id"))
    vehicle = _clean(rec.get("vehicleid") or rec.get("vehicle"))
    # accountId is REQUIRED per row — deliberately no default fallback, so a
    # missing value stops that row instead of silently using someone else's
    # account.
    account = _clean(rec.get("accountid") or rec.get("account"))
    if not device:
        return None, f"{where}: missing deviceId."
    if not device.isdigit():
        return None, f"{where}: deviceId '{device}' must be numeric (IMEI)."
    if not vehicle:
        return None, f"{where}: missing vehicleId."
    if not vehicle.isdigit():
        return None, f"{where}: vehicleId '{vehicle}' must be numeric."
    if not account:
        return None, f"{where}: accountId is required."
    return {
        "device_id": device,
        "asset_id": device,                 # assetId == deviceId (confirmed)
        "vehicle_id": int(vehicle),
        "account_id": account,
        "sim": _clean(rec.get("sim")),
        "serial_number": _clean(rec.get("serialnumber") or rec.get("serial")),
        # per-row value wins; otherwise the run-level default
        "device_type": _clean(rec.get("devicetype")) or _clean(defaults.get("deviceType")),
    }, None


def build_onboard_device(row):
    """Step 1 body — must match the standalone Device Add tab exactly.

    id/imei are sent as INTEGERS (the working Camera Quick Add does
    ``int(imei)``); sending them as strings was the one difference between
    this flow and the tab that works, and it produced 409 responses.
    deviceSupplier stays CLIENT (unchanged by request).
    """
    try:
        dev = int(row["device_id"])
    except (TypeError, ValueError):
        dev = row["device_id"]
    payload = {"id": dev, "imei": dev,
               "deviceType": row["device_type"], "deviceSupplier": "CLIENT",
               "sim": row["sim"], "mobile": row["sim"],
               "serialNumber": row["serial_number"]}
    return {k: v for k, v in payload.items() if v not in (None, "", "None")}


def build_onboard_asset(row, defaults):
    """Step 2 body. assetId/name/productId all equal the device id."""
    aid = int(row["asset_id"])
    return {"assetId": aid, "name": aid, "productId": aid,
            "supplier": _clean(defaults.get("assetSupplier")) or "CLIENT",
            "model": _clean(defaults.get("assetModel")),
            "type": _clean(defaults.get("assetType")),
            "status": "ACTIVE",
            "issuedToUserId": int(_clean(defaults.get("issuedToUserId")) or 0)}
