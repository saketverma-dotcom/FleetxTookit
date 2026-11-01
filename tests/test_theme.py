"""Application theme (v4.0).

The theme must be able to change colours without ever creating or destroying a
widget — custom drawing is what caused this app's lag, dead-click and hang
regressions, so the theme layer deliberately only sets style options.
"""
from fleetx_toolkit import theme as TH


class TestPalettes:
    def test_both_themes_exist(self):
        assert set(TH.THEMES) == {"dark", "classic"}

    def test_dark_uses_fleetx_green(self):
        assert TH.DARK["accent"] == "#0FB968"

    def test_background_is_lifted_off_black(self):
        """Pure black is harsh over a long shift; the base is a dark slate."""
        assert TH.DARK["bg"] == "#131A21" and TH.DARK["bg"] != "#000000"

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
        assert TH.c("accent") == "#0FB968"
        TH._active = TH.CLASSIC
        assert TH.c("accent") == "#0A8F4D"

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
