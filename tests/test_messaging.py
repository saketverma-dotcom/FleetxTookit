"""Tests for Messaging tab logic (v3.5). No Tk, no network."""
from fleetx_toolkit import messaging as M


SAMPLE = {"count": 2, "data": [
    {"id": 15, "phone": "+9198", "date": "2026-01-09 13:03", "msg": "Hello", "id_device": 350374},
    {"id": 16, "phone": "+9198", "date": "2026-01-09 13:05", "msg": "You there?", "id_device": 350374},
    {"id": "bad"},  # malformed row must be skipped
]}


class TestRequests:
    def test_inbox_no_since(self):
        url, params = M.build_inbox_request("TOK", "350374")
        assert url.endswith("inbox_sms.php")
        assert params == {"token": "TOK", "device": "350374"}

    def test_inbox_since_uses_start_id_plus_one(self):
        _, params = M.build_inbox_request("TOK", "350374", since_id=100)
        assert params["start_id"] == "101"

    def test_reply_targets_device(self):
        url, data = M.build_reply_request("TOK", "352969", "+9199", "hi")
        assert url.endswith("sms.php")
        assert data == {"token": "TOK", "device": "352969",
                        "phone": "+9199", "msg": "hi"}


class TestParseInbox:
    def test_parses_and_skips_bad_rows(self):
        msgs = M.parse_inbox(SAMPLE)
        assert [m["id"] for m in msgs] == [15, 16]
        assert msgs[1]["msg"] == "You there?"
        assert msgs[0]["device"] == "350374"

    def test_garbage_input(self):
        assert M.parse_inbox("nope") == []
        assert M.parse_inbox({}) == []
        assert M.parse_inbox({"data": None}) == []


class TestHighWater:
    def test_max_id(self):
        msgs = M.parse_inbox(SAMPLE)
        assert M.max_id(msgs) == 16
        assert M.max_id(msgs, current=20) == 20
        assert M.max_id([], current=5) == 5

    def test_new_since(self):
        msgs = M.parse_inbox(SAMPLE)
        assert [m["id"] for m in M.new_since(msgs, 15)] == [16]
        assert M.new_since(msgs, 16) == []
        assert [m["id"] for m in M.new_since(msgs, 0)] == [15, 16]


class TestThreads:
    def test_group_and_dedupe_incoming(self):
        msgs = M.parse_inbox(SAMPLE)
        t = {}
        M.add_messages(t, msgs, "in")
        assert list(t) == ["+9198"] and len(t["+9198"]) == 2
        M.add_messages(t, msgs, "in")           # same ids again
        assert len(t["+9198"]) == 2             # deduped

    def test_outgoing_appended(self):
        t = {}
        M.add_messages(t, M.parse_inbox(SAMPLE), "in")   # ids 15,16 dated 13:03/13:05
        M.add_messages(t, [{"id": 500, "phone": "+9198", "msg": "reply",
                            "date": "2026-01-09 13:10"}], "out")
        conv = M.conversation(t, "+9198")
        assert conv[-1]["dir"] == "out" and conv[-1]["msg"] == "reply"  # latest by time
        assert conv[0]["id"] == 15                                       # earliest incoming

    def test_thread_order_by_recency(self):
        t = {}
        M.add_messages(t, M.parse_inbox(SAMPLE), "in")   # +9198 latest 13:05
        M.add_messages(t, [{"id": 20, "phone": "+9111", "msg": "hey",
                            "date": "2026-01-09 13:30"}], "in")
        assert M.thread_order(t)[0] == "+9111"           # later timestamp first

    def test_blank_phone_ignored(self):
        t = {}
        M.add_messages(t, [{"id": 1, "phone": "", "msg": "x"}], "in")
        assert t == {}

    def test_conversation_missing_phone(self):
        assert M.conversation({}, "+000") == []


class TestOutboxStatus:
    def test_status_precedence(self):
        assert M.status_from_row({"is_delivered": 1, "is_send": 1}) == M.SENT_DELIVERED
        assert M.status_from_row({"is_send": 1}) == M.SENT_SENT
        assert M.status_from_row({}) == M.SENT_PENDING
        assert M.status_from_row({"is_error": 1}) == M.SENT_FAILED
        assert M.status_from_row({"is_error_send": 1}) == M.SENT_FAILED
        assert M.status_from_row({"is_cancel": 1}) == M.SENT_CANCELLED

    def test_error_beats_delivered(self):
        assert M.status_from_row({"is_delivered": 1, "is_error": 1}) == M.SENT_FAILED

    def test_parse_outbox(self):
        r = {"data": [{"id": 371, "is_send": 1, "is_delivered": 1},
                      {"id": 372, "is_send": 1},
                      {"id": "bad"}]}
        assert M.parse_outbox_status(r) == {371: M.SENT_DELIVERED, 372: M.SENT_SENT}
        assert M.parse_outbox_status("x") == {}

    def test_outbox_request(self):
        url, params = M.build_outbox_request("TOK", "350374", [371, 372])
        assert url.endswith("outbox_sms.php")
        assert params["list_id"] == "371,372"

    def test_terminal_set(self):
        assert M.SENT_DELIVERED in M.TERMINAL_STATUSES
        assert M.SENT_FAILED in M.TERMINAL_STATUSES
        assert M.SENT_PENDING not in M.TERMINAL_STATUSES
        assert M.SENT_SENT not in M.TERMINAL_STATUSES

    def test_every_status_has_label(self):
        for s in (M.SENT_PENDING, M.SENT_SENT, M.SENT_DELIVERED,
                  M.SENT_FAILED, M.SENT_CANCELLED):
            assert M.STATUS_LABEL[s]


class TestTimestampSort:
    def test_conversation_interleaves_by_time(self):
        t = {}
        M.add_messages(t, [{"id": 16, "phone": "+91", "msg": "in-later",
                            "date": "2026-01-09 13:05"}], "in")
        M.add_messages(t, [{"id": 0, "phone": "+91", "msg": "out-earlier",
                            "date": "2026-01-09 13:04"}], "out")
        conv = M.conversation(t, "+91")
        assert [m["msg"] for m in conv] == ["out-earlier", "in-later"]

    def test_thread_order_by_latest_timestamp(self):
        t = {}
        M.add_messages(t, [{"id": 99, "phone": "+91", "msg": "old",
                            "date": "2026-01-09 10:00"}], "in")
        M.add_messages(t, [{"id": 1, "phone": "+92", "msg": "new",
                            "date": "2026-01-09 14:00"}], "in")
        assert M.thread_order(t)[0] == "+92"   # newer timestamp wins despite lower id
