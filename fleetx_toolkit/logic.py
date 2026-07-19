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
