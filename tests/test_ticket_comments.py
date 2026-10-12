"""Bulk ticket comments + reassignment (v3.18).

Request shapes verified against the supplied curl:
  POST /api/v1/internal/comment/
       {"title":"","internal":true,"commentableType":"INTERNAL_ISSUE",
        "commentableId":407582,"comment":"<p>…</p>\\n",
        "eventTime":"2026-10-12 12:52:30","commentImages":[]}
  PUT  /api/v1/internal/issue/change-assignment  {"id":…,"assignedToId":…}
"""
from fleetx_toolkit import logic as L


class TestCommentHtml:
    def test_plain_text_becomes_paragraph(self):
        assert L.comment_to_html("Hello") == "<p>Hello</p>\n"

    def test_each_line_is_its_own_paragraph(self):
        assert L.comment_to_html("a\nb") == "<p>a</p>\n<p>b</p>\n"

    def test_blank_lines_dropped(self):
        assert L.comment_to_html("a\n\n\nb") == "<p>a</p>\n<p>b</p>\n"

    def test_existing_html_passes_through(self):
        assert L.comment_to_html("<p>already</p>") == "<p>already</p>"

    def test_angle_brackets_escaped_in_plain_text(self):
        out = L.comment_to_html("value < 5 & rising")
        assert "&lt;" in out and "&amp;" in out

    def test_empty(self):
        assert L.comment_to_html("") == "" and L.comment_to_html("   ") == ""


class TestBodies:
    def test_comment_body_matches_curl(self):
        b = L.build_comment_body(407582, "<p>x</p>\n", "2026-10-12 12:52:30")
        assert b == {"title": "", "internal": True,
                     "commentableType": "INTERNAL_ISSUE",
                     "commentableId": 407582, "comment": "<p>x</p>\n",
                     "eventTime": "2026-10-12 12:52:30", "commentImages": []}

    def test_ids_are_integers(self):
        b = L.build_comment_body("407582", "x", "t")
        assert isinstance(b["commentableId"], int)
        a = L.build_reassign_body("407582", "880971")
        assert a == {"id": 407582, "assignedToId": 880971}
        assert isinstance(a["id"], int) and isinstance(a["assignedToId"], int)


class TestTicketIds:
    def test_separators(self):
        ids, errs = L.parse_ticket_ids("407582, 407583\n407584 407585")
        assert ids == ["407582", "407583", "407584", "407585"] and not errs

    def test_duplicates_collapsed_order_kept(self):
        ids, _ = L.parse_ticket_ids("3,1,3,2")
        assert ids == ["3", "1", "2"]

    def test_non_numeric_reported(self):
        ids, errs = L.parse_ticket_ids("407582, abc")
        assert ids == ["407582"] and "not a numeric" in errs[0]

    def test_empty(self):
        ids, errs = L.parse_ticket_ids("   ")
        assert ids == [] and errs == []


class TestExcelRows:
    def test_columns_and_fallback(self):
        recs = [{"ticketId": 407582, "comment": "a", "assignTo": 880971},
                {"ticketId": 407583, "comment": "b"}]
        rows, errs = L.parse_comment_records(recs, default_assignee="999")
        assert not errs
        assert rows[0]["assignee"] == "880971"      # sheet wins
        assert rows[1]["assignee"] == "999"         # falls back

    def test_missing_comment_rejected(self):
        rows, errs = L.parse_comment_records([{"ticketId": 1}], "")
        assert not rows and "missing comment" in errs[0]

    def test_bad_ticket_id_rejected(self):
        rows, errs = L.parse_comment_records([{"ticketId": "xx", "comment": "c"}], "")
        assert not rows and "must be numeric" in errs[0]

    def test_non_numeric_assignee_rejected(self):
        rows, errs = L.parse_comment_records(
            [{"ticketId": 1, "comment": "c", "assignTo": "komal"}], "")
        assert not rows and "numeric user id" in errs[0]

    def test_row_numbers_reported(self):
        rows, errs = L.parse_comment_records([{"comment": "c"}], "")
        assert "Row 2" in errs[0]

    def test_alternate_column_names(self):
        recs = [{"issueId": 5, "remark": "note", "assignedToId": 7}]
        rows, errs = L.parse_comment_records(recs, "")
        assert rows == [{"ticket_id": "5", "comment": "note", "assignee": "7"}]


class TestAccess:
    def test_tab_appears_in_access_grid(self):
        """Grantable from User Access (Admin), like the other controllable tabs."""
        from fleetx_toolkit.config import CONTROLLABLE_TABS
        assert "Ticket Comments" in CONTROLLABLE_TABS

    def test_admin_gets_it_automatically(self):
        from fleetx_toolkit.access_control import allowed_tabs_for
        assert "Ticket Comments" in allowed_tabs_for("saket.verma@fleetx.io")

    def test_non_admin_needs_an_explicit_grant(self):
        from fleetx_toolkit import access_control as ac
        ac.set_snapshot({"komal@fleetx.io": ["Tickets"]})
        assert "Ticket Comments" not in ac.allowed_tabs_for("komal@fleetx.io")
        ac.set_snapshot({"komal@fleetx.io": ["Tickets", "Ticket Comments"]})
        assert "Ticket Comments" in ac.allowed_tabs_for("komal@fleetx.io")
