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
    # Contrast is tuned so each layer is clearly a step apart: the page is the
    # darkest, panels sit above it, and INPUTS ARE LIGHTER THAN THEIR PANEL so
    # a text box reads as a text box without having to hunt for its border.
    "bg": "#121922",          # window / tab page
    "panel": "#1B242E",       # LabelFrame, cards
    "panel2": "#374657",      # inputs, table headers — deliberately lighter
    "raised": "#3C4C5D",      # secondary buttons
    "line": "#5E7285",        # visible border, not a hairline
    "line_soft": "#374657",
    "ink": "#F0F5FA",
    "ink_dim": "#AEBCC9",     # readable secondary text
    "ink_faint": "#8795A3",   # hints still legible
    "accent": "#19C37D",      # Fleetx green — brand + primary action
    "accent_hi": "#2BD991",
    "accent_ink": "#04130B",  # text on an accent fill
    "accent_ghost": "#153624",
    "ok": "#56D364",          # status green, deliberately NOT the brand green
    "warn": "#E3B341",
    "bad": "#FF7B72",
    "info": "#79B8FF",
    "skip": "#8795A3",
    "log_bg": "#0D141B",
    "sel_bg": "#1D4030",
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
                    lightcolor=p["line"], darkcolor=p["line"], borderwidth=1,
                    relief="solid")
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
                    bordercolor=p["line"], insertcolor=p["ink"], padding=5,
                    borderwidth=1, relief="solid")
    style.map("TEntry", bordercolor=[("focus", p["accent"])])
    style.configure("TCombobox", fieldbackground=p["panel2"], background=p["panel2"],
                    foreground=p["ink"], arrowcolor=p["ink_dim"], bordercolor=p["line"],
                    padding=3)
    style.map("TCombobox",
              fieldbackground=[("readonly", p["panel2"])],
              foreground=[("readonly", p["ink"])],
              bordercolor=[("focus", p["accent"])])

    style.configure("TNotebook", background=p["bg"], bordercolor=p["line"], tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=p["panel"], foreground=p["ink_dim"],
                    bordercolor=p["line"], padding=(14, 8), font=("Segoe UI", 9))
    style.map("TNotebook.Tab",
              background=[("selected", p["panel2"]), ("active", p["raised"])],
              foreground=[("selected", p["accent"]), ("active", p["ink"])],
              expand=[("selected", (1, 1, 1, 0))])

    style.configure("TScrollbar", background=p["raised"], troughcolor=p["panel"],
                    bordercolor=p["line"], arrowcolor=p["ink"], width=13)
    style.map("TScrollbar", background=[("active", p["line"])])
    style.configure("TPanedwindow", background=p["bg"])
    style.configure("Sash", background=p["line"], gripcount=0, sashthickness=5)
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
    # A 1px highlight border is what makes a dark Text/Listbox read as an
    # input instead of a hole in the panel.
    edge = {"highlightthickness": 1, "highlightbackground": p["line"],
            "highlightcolor": p["accent"], "borderwidth": 0, "relief": "flat"}
    if kind == "log":
        return dict(edge, background=p["log_bg"], foreground=p["ink"],
                    insertbackground=p["ink"], selectbackground=p["sel_bg"])
    if kind == "listbox":
        return dict(edge, background=p["panel2"], foreground=p["ink"],
                    selectbackground=p["sel_bg"], selectforeground=p["ink"])
    return dict(edge, background=p["panel2"], foreground=p["ink"],
                insertbackground=p["ink"], selectbackground=p["sel_bg"])
