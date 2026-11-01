"""Application theme (v4.0).

The theme must be able to change colours without ever creating or destroying a
widget — custom drawing is what caused this app's lag, dead-click and hang
regressions, so the theme layer deliberately only sets style options.
"""
from fleetx_toolkit import theme as TH


class TestPalettes:
    def test_both_themes_exist(self):
        assert set(TH.THEMES) == {"dark", "classic"}

    def test_dark_uses_a_fleetx_green_accent(self):
        """Pin the property, not the hex — the exact tone gets tuned."""
        h = TH.DARK["accent"].lstrip("#")
        r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
        assert g > r and g > b and g > 120          # clearly green, not dim

    def test_background_is_lifted_off_black(self):
        """Pure black is harsh over a long shift; the base is a dark slate."""
        h = TH.DARK["bg"].lstrip("#")
        assert sum(int(h[i:i+2], 16) for i in (0, 2, 4)) > 30

    def test_brand_green_differs_from_status_green(self):
        """A primary button must never read as 'this row succeeded'."""
        assert TH.DARK["accent"] != TH.DARK["ok"]

    def test_every_key_present_in_both(self):
        assert set(TH.DARK) == set(TH.CLASSIC)

    def test_status_colours_are_distinct(self):
        d = TH.DARK
        assert len({d["ok"], d["warn"], d["bad"], d["skip"], d["info"]}) == 5


class TestLookup:
    def test_default_is_classic(self):
        TH._active = TH.CLASSIC
        assert not TH.is_dark()
        assert TH.palette() is TH.CLASSIC

    def test_c_returns_from_active_palette(self):
        TH._active = TH.DARK
        assert TH.c("accent") == TH.DARK["accent"]
        TH._active = TH.CLASSIC
        assert TH.c("accent") == TH.CLASSIC["accent"]
        assert TH.DARK["accent"] != TH.CLASSIC["accent"]

    def test_c_falls_back_for_unknown_key(self):
        assert TH.c("no-such-key", "fallback") == "fallback"


class TestTkWidgetColors:
    def test_classic_returns_nothing_to_override(self):
        TH._active = TH.CLASSIC
        assert TH.tk_widget_colors("log") == {}

    def test_dark_supplies_log_colours(self):
        TH._active = TH.DARK
        kw = TH.tk_widget_colors("log")
        assert kw["background"] == TH.DARK["log_bg"]
        assert kw["foreground"] == TH.DARK["ink"]

    def test_dark_supplies_listbox_colours(self):
        TH._active = TH.DARK
        kw = TH.tk_widget_colors("listbox")
        assert kw["selectbackground"] == TH.DARK["sel_bg"]

    def teardown_method(self):
        TH._active = TH.CLASSIC


def _lum(hexcol):
    h = hexcol.lstrip("#")
    ch = [int(h[i:i+2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (f(c) for c in ch)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    l1, l2 = sorted((_lum(a), _lum(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


class TestDarkContrast:
    """Guards so a later colour tweak cannot quietly make the UI unreadable.
    These thresholds come from the readability problems reported on v4.0."""

    def test_body_text_is_readable(self):
        assert _ratio(TH.DARK["ink"], TH.DARK["panel"]) >= 7

    def test_secondary_text_is_readable(self):
        assert _ratio(TH.DARK["ink_dim"], TH.DARK["panel"]) >= 4.5

    def test_hint_text_is_still_legible(self):
        assert _ratio(TH.DARK["ink_faint"], TH.DARK["bg"]) >= 4.5

    def test_text_inside_an_input_is_readable(self):
        assert _ratio(TH.DARK["ink"], TH.DARK["panel2"]) >= 7

    def test_inputs_stand_out_from_their_panel(self):
        """v4.0 shipped at 1.31 — boxes vanished into the panel."""
        assert _ratio(TH.DARK["panel2"], TH.DARK["panel"]) >= 1.5

    def test_borders_are_visible(self):
        assert _ratio(TH.DARK["line"], TH.DARK["panel"]) >= 2.5

    def test_accent_button_label_is_readable(self):
        assert _ratio(TH.DARK["accent_ink"], TH.DARK["accent"]) >= 4.5

    def test_status_colours_readable_on_panel(self):
        for k in ("ok", "warn", "bad", "info"):
            assert _ratio(TH.DARK[k], TH.DARK["panel"]) >= 4.5, k


class TestClassicTkWidgetsAreCovered:
    """v4.0 shipped with 13 ScrolledText boxes still white on dark: ttk.Style
    cannot reach classic tk widgets, and the recolour sweep was scheduled with
    after_idle so it had not run by the time the window was shown. The sweep
    now runs immediately after the tabs are built."""

    def test_every_widget_kind_gets_colours(self):
        TH._active = TH.DARK
        for kind in ("text", "log", "listbox"):
            kw = TH.tk_widget_colors(kind)
            assert kw["background"] != "white"
            assert kw["foreground"] == TH.DARK["ink"]

    def test_text_widgets_get_a_visible_border(self):
        """Without this a dark Text reads as a hole in the panel."""
        TH._active = TH.DARK
        for kind in ("text", "log", "listbox"):
            kw = TH.tk_widget_colors(kind)
            assert kw["highlightthickness"] == 1
            assert kw["highlightbackground"] == TH.DARK["line"]

    def test_focus_ring_uses_the_accent(self):
        TH._active = TH.DARK
        assert TH.tk_widget_colors("text")["highlightcolor"] == TH.DARK["accent"]

    def test_classic_leaves_widgets_alone(self):
        TH._active = TH.CLASSIC
        assert TH.tk_widget_colors("text") == {}

    def teardown_method(self):
        TH._active = TH.CLASSIC


class TestContentSurface:
    """v4.0.2: the message thread is a reading surface, not an input.

    It was rendering on panel2 (the input colour), which is LIGHTER than the
    conversation list beside it — inverting the hierarchy and making the pane
    look like a text field.
    """

    def test_content_sits_on_the_panel_colour(self):
        TH._active = TH.DARK
        assert TH.tk_widget_colors("content")["background"] == TH.DARK["panel"]

    def test_content_is_darker_than_an_input(self):
        TH._active = TH.DARK
        content = TH.tk_widget_colors("content")["background"]
        field = TH.tk_widget_colors("text")["background"]
        assert content != field
        assert _lum(content) < _lum(field)

    def test_content_has_no_input_border(self):
        """A border would make the reading pane look like a field again."""
        TH._active = TH.DARK
        assert TH.tk_widget_colors("content")["highlightthickness"] == 0

    def test_content_text_stays_readable(self):
        TH._active = TH.DARK
        kw = TH.tk_widget_colors("content")
        assert _ratio(kw["foreground"], kw["background"]) >= 7

    def test_timestamps_recede_but_stay_legible(self):
        """Timestamps use ink_faint — quieter than body text, still >= 4.5:1."""
        TH._active = TH.DARK
        assert _ratio(TH.DARK["ink_faint"], TH.DARK["panel"]) >= 4.5
        assert _lum(TH.DARK["ink_faint"]) < _lum(TH.DARK["ink"])

    def teardown_method(self):
        TH._active = TH.CLASSIC
