"""Application theme (v4.0).

One module holds every colour and the ttk.Style wiring, so a tab never has to
know what theme is active — it asks for a palette entry by name.

Design constraints, kept deliberately narrow because this app's rendering
regressions have all come from custom drawing, never from theming:

  • stock ttk widgets only — no canvas-drawn rows, no owner-draw
  • flat fills, 1px borders, square corners (what ttk renders well)
  • Windows-available fonts only
  • switching theme never rebuilds a widget tree; it restyles in place

CLASSIC is the stock Windows look the tool shipped with and remains the
default, so any tab that still carries a hardcoded colour keeps working while
DARK is proven in the field.
"""

DARK = {
    "name": "dark",
    "bg": "#131A21",          # window / tab page
    "panel": "#1A222B",       # LabelFrame, cards
    "panel2": "#202A34",      # inputs, table headers
    "raised": "#26313C",      # secondary buttons
    "line": "#2A3440",
    "line_soft": "#222C36",
    "ink": "#E6EDF3",
    "ink_dim": "#8B98A5",
    "ink_faint": "#5E6B78",
    "accent": "#0FB968",      # Fleetx green — brand + primary action
    "accent_hi": "#18D278",
    "accent_ink": "#06130C",  # text on an accent fill
    "accent_ghost": "#12301F",
    "ok": "#3FB950",          # status green, deliberately NOT the brand green
    "warn": "#D9A520",
    "bad": "#F2544B",
    "info": "#4C9AFF",
    "skip": "#6E7B89",
    "log_bg": "#0F161C",
    "sel_bg": "#12301F",
}

CLASSIC = {
    "name": "classic",
    "bg": "SystemButtonFace",
    "panel": "SystemButtonFace",
    "panel2": "white",
    "raised": "SystemButtonFace",
    "line": "#A0A0A0",
    "line_soft": "#D0D0D0",
    "ink": "black",
    "ink_dim": "#555555",
    "ink_faint": "gray",
    "accent": "#0A8F4D",
    "accent_hi": "#0FB968",
    "accent_ink": "white",
    "accent_ghost": "#E8F5EE",
    "ok": "green",
    "warn": "#B26A00",
    "bad": "red",
    "info": "blue",
    "skip": "gray",
    "log_bg": "white",
    "sel_bg": "#E8F5EE",
}

THEMES = {"dark": DARK, "classic": CLASSIC}
_active = CLASSIC


def palette():
    """The colours currently in force. Tabs read from this, never literals."""
    return _active


def c(key, default="black"):
    return _active.get(key, default)


def is_dark():
    return _active is DARK


def apply_theme(root, name="classic"):
    """Point ttk at a palette and restyle in place.

    Returns the palette. Safe to call repeatedly — it changes style options and
    widget colours, it never creates or destroys widgets, so it cannot trigger
    the rebuild/dead-click/hang problems that custom row drawing caused.
    """
    global _active
    _active = THEMES.get(str(name).lower(), CLASSIC)
    p = _active
    from tkinter import ttk
    style = ttk.Style(root)

    if p is CLASSIC:
        try:
            style.theme_use("vista")
        except Exception:
            try:
                style.theme_use("default")
            except Exception:
                pass
        try:
            root.configure(bg=p["bg"])
        except Exception:
            pass
        _style_tree(style, p)
        return p

    # "clam" is the only built-in theme that honours background/fieldbackground
    # on enough widgets for a dark scheme to look coherent.
    try:
        style.theme_use("clam")
    except Exception:
        pass
    try:
        root.configure(bg=p["bg"])
    except Exception:
        pass

    base = {"background": p["bg"], "foreground": p["ink"],
            "fieldbackground": p["panel2"], "bordercolor": p["line"],
            "lightcolor": p["line"], "darkcolor": p["line"],
            "troughcolor": p["panel2"], "focuscolor": p["accent"]}
    style.configure(".", **base)

    style.configure("TFrame", background=p["bg"])
    style.configure("TLabel", background=p["bg"], foreground=p["ink"])
    style.configure("TLabelframe", background=p["panel"], bordercolor=p["line"],
                    lightcolor=p["line"], darkcolor=p["line"])
    style.configure("TLabelframe.Label", background=p["panel"], foreground=p["ink_dim"])
    style.configure("TCheckbutton", background=p["bg"], foreground=p["ink"],
                    focuscolor=p["accent"])
    style.map("TCheckbutton",
              background=[("active", p["bg"])],
              indicatorcolor=[("selected", p["accent"]), ("!selected", p["panel2"])])
    style.configure("TRadiobutton", background=p["bg"], foreground=p["ink"])
    style.map("TRadiobutton",
              background=[("active", p["bg"])],
              indicatorcolor=[("selected", p["accent"]), ("!selected", p["panel2"])])

    # buttons: secondary by default, Accent.TButton for the primary action
    style.configure("TButton", background=p["raised"], foreground=p["ink"],
                    bordercolor=p["line"], focuscolor=p["accent"], padding=(10, 5))
    style.map("TButton",
              background=[("pressed", p["panel2"]), ("active", p["line"])],
              foreground=[("disabled", p["ink_faint"])])
    style.configure("Accent.TButton", background=p["accent"], foreground=p["accent_ink"],
                    bordercolor=p["accent"], padding=(12, 5))
    style.map("Accent.TButton",
              background=[("pressed", p["accent"]), ("active", p["accent_hi"]),
                          ("disabled", p["raised"])],
              foreground=[("disabled", p["ink_faint"])])
    style.configure("Danger.TButton", background=p["raised"], foreground=p["bad"],
                    bordercolor=p["line"])

    style.configure("TEntry", fieldbackground=p["panel2"], foreground=p["ink"],
                    bordercolor=p["line"], insertcolor=p["ink"], padding=4)
    style.map("TEntry", bordercolor=[("focus", p["accent"])])
    style.configure("TCombobox", fieldbackground=p["panel2"], background=p["panel2"],
                    foreground=p["ink"], arrowcolor=p["ink_dim"], bordercolor=p["line"],
                    padding=3)
    style.map("TCombobox",
              fieldbackground=[("readonly", p["panel2"])],
              foreground=[("readonly", p["ink"])],
              bordercolor=[("focus", p["accent"])])

    style.configure("TNotebook", background=p["bg"], bordercolor=p["line"], tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=p["panel2"], foreground=p["ink_dim"],
                    bordercolor=p["line"], padding=(13, 7))
    style.map("TNotebook.Tab",
              background=[("selected", p["bg"])],
              foreground=[("selected", p["ink"])],
              expand=[("selected", (0, 0, 0, 1))])

    style.configure("TScrollbar", background=p["raised"], troughcolor=p["bg"],
                    bordercolor=p["line"], arrowcolor=p["ink_dim"])
    style.configure("TPanedwindow", background=p["bg"])
    style.configure("Sash", background=p["line"], gripcount=0)
    style.configure("TSeparator", background=p["line"])
    style.configure("Hint.TLabel", background=p["bg"], foreground=p["ink_faint"])
    style.configure("Head.TLabel", background=p["bg"], foreground=p["ink"])
    _style_tree(style, p)
    return p


def _style_tree(style, p):
    """Treeview carries the run grid and the SMS delivery table."""
    style.configure("Treeview",
                    background=p["panel"] if p is not CLASSIC else "white",
                    fieldbackground=p["panel"] if p is not CLASSIC else "white",
                    foreground=p["ink"], bordercolor=p["line"], rowheight=23)
    style.configure("Treeview.Heading",
                    background=p["panel2"], foreground=p["ink_dim"],
                    bordercolor=p["line"], relief="flat")
    style.map("Treeview.Heading", background=[("active", p["raised"])])
    style.map("Treeview",
              background=[("selected", p["sel_bg"])],
              foreground=[("selected", p["ink"])])


def tk_widget_colors(kind="text"):
    """Colours for the classic tk widgets that ttk cannot style — Text,
    Listbox, Canvas. Returned as kwargs to pass straight to configure()."""
    p = _active
    if p is CLASSIC:
        return {}
    if kind == "log":
        return {"background": p["log_bg"], "foreground": p["ink"],
                "insertbackground": p["ink"], "highlightthickness": 0,
                "selectbackground": p["sel_bg"]}
    if kind == "listbox":
        return {"background": p["panel"], "foreground": p["ink"],
                "selectbackground": p["sel_bg"], "selectforeground": p["ink"],
                "highlightthickness": 0, "borderwidth": 0}
    return {"background": p["panel2"], "foreground": p["ink"],
            "insertbackground": p["ink"], "highlightthickness": 0,
            "selectbackground": p["sel_bg"]}
