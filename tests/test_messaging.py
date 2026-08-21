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


class TestSegments:
    def test_empty(self):
        assert M.sms_segments("") == (0, 0)

    def test_gsm_single(self):
        assert M.sms_segments("hello") == (5, 1)
        assert M.sms_segments("a" * 160) == (160, 1)

    def test_gsm_multi(self):
        assert M.sms_segments("a" * 161) == (161, 2)
        assert M.sms_segments("a" * 306) == (306, 2)
        assert M.sms_segments("a" * 307) == (307, 3)

    def test_unicode_single(self):
        assert M.sms_segments("😀") == (1, 1)

    def test_unicode_multi(self):
        assert M.sms_segments("é" * 71) == (71, 2)


class TestUIHelpers:
    def test_avatar_color_stable_and_in_palette(self):
        c = M.avatar_color("+915755201963999")
        assert c == M.avatar_color("+915755201963999")
        assert c in M.AVATAR_COLORS

    def test_avatar_initials(self):
        assert M.avatar_initials("+915755201963999") == "99"
        assert M.avatar_initials("5") == "5"
        assert M.avatar_initials("") == "?"

    def test_preview_and_time(self):
        t = {"+9198": [{"id": 1, "phone": "+9198", "msg": "CONFIGURATION SAVED",
                        "date": "2026-01-09 15:59", "dir": "in"}]}
        assert M.preview_text(t, "+9198") == "CONFIGURATION SAVED"
        assert M.last_time(t, "+9198") == "15:59"
        assert M.preview_text({}, "+000") == "(new conversation)"
        assert M.last_time({}, "+000") == ""

    def test_preview_truncates(self):
        t = {"+9198": [{"id": 1, "phone": "+9198", "msg": "x" * 100,
                        "date": "2026-01-09 15:59", "dir": "in"}]}
        p = M.preview_text(t, "+9198", limit=38)
        assert len(p) == 39 and p.endswith("…")

    def test_filter_by_query(self):
        t = {"+9198": [{"id": 1, "phone": "+9198", "msg": "CONFIG SAVED",
                        "date": "2026-01-09 15:00", "dir": "in"}],
             "+9111": [{"id": 2, "phone": "+9111", "msg": "SET OK",
                        "date": "2026-01-09 16:00", "dir": "in"}]}
        assert M.filter_threads(t, "config") == ["+9198"]
        assert M.filter_threads(t, "9111") == ["+9111"]
        assert set(M.filter_threads(t, "")) == {"+9198", "+9111"}

    def test_filter_by_unread(self):
        t = {"+9198": [{"id": 1, "phone": "+9198", "msg": "a",
                        "date": "2026-01-09 15:00", "dir": "in"}],
             "+9111": [{"id": 2, "phone": "+9111", "msg": "b",
                        "date": "2026-01-09 16:00", "dir": "in"}]}
        assert M.filter_threads(t, "", unread={"+9111"}) == ["+9111"]


class TestPollBackoff:
    def test_active_stays_fast(self):
        assert M.next_poll_interval(0) == 7
        assert M.next_poll_interval(4) == 7

    def test_steps_up_when_idle(self):
        assert M.next_poll_interval(5) == 15
        assert M.next_poll_interval(14) == 15
        assert M.next_poll_interval(15) == 30

    def test_capped(self):
        assert M.next_poll_interval(1000) == M.POLL_MAX == 30


class TestFriendlyErrors:
    """Poll failures must be readable and non-alarming — the raw
    HTTPSConnectionPool traceback text meant nothing to users."""

    def test_read_timeout(self):
        import requests
        msg = M.friendly_error(requests.exceptions.ReadTimeout(
            "HTTPSConnectionPool(host='semysms.net', port=443): Read timed out. (read timeout=20)"))
        assert "slow to respond" in msg
        assert "HTTPSConnectionPool" not in msg     # no raw internals

    def test_no_internet(self):
        assert "No internet" in M.friendly_error(
            Exception("Temporary failure in name resolution"))

    def test_connection_error(self):
        import requests
        assert "Can't reach" in M.friendly_error(
            requests.exceptions.ConnectionError("Connection refused"))

    def test_bad_json(self):
        assert "unexpected response" in M.friendly_error(
            ValueError("Expecting value: line 1 column 1 (char 0)"))

    def test_unknown_falls_back(self):
        assert "still retrying" in M.friendly_error(Exception("weird"))

    def test_all_messages_reassure_retry(self):
        import requests
        for e in (requests.exceptions.ReadTimeout("Read timed out"),
                  requests.exceptions.ConnectionError("refused"),
                  ValueError("Expecting value"),
                  Exception("other")):
            m = M.friendly_error(e)
            assert "retrying" in m or "check your connection" in m
