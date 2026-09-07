"""Icy theming and semantic state presentation for RepoManager."""
from tkinter import ttk

PALETTES = {
    "dark": {
        "bg":        "#0b1118",
        "panel":     "#111a24",
        "panel2":    "#182432",
        "header":    "#0e1721",
        "text":      "#dceaf5",
        "muted":     "#8ca0b3",
        "accent":    "#49b7e8",
        "accent2":   "#8bdcff",
        "selection": "#174b66",
        "selection_fg": "#ffffff",
        "border":    "#263748",
        "danger":    "#ff6b70",
        "ok":        "#5fd399",
        "warning":   "#f2bd5b",
        "activity":  "#55c7f2",
        "neutral":   "#8da0b3",
        "error_bg": "#321a20", "error_border": "#71313b",
        "success_bg": "#113026", "success_border": "#2c694e",
        "warning_bg": "#332815", "warning_border": "#73571f",
        "activity_bg": "#122b38", "activity_border": "#28647c",
        "neutral_bg": "#1a2430", "neutral_border": "#354658",
        # row state colors
        "row_dirty_bg":  "#332815",
        "row_dirty_fg":  "#f2bd5b",
        "row_sync_fg":   "#55c7f2",
        "row_norem_fg":  "#f2bd5b",
        "row_arch_fg":   "#6f8293",
        "stripe":        "#121924",
    },
    "light": {
        # Ice Light deliberately avoids white application surfaces.
        "bg":        "#e8f2f5",
        "panel":     "#f4fafb",
        "panel2":    "#dcebef",
        "header":    "#d3e7ec",
        "text":      "#132a38",
        "muted":     "#536e7d",
        "accent":    "#0a779d",
        "accent2":   "#075f7e",
        "selection": "#b9e4ef",
        "selection_fg": "#132a38",
        "border":    "#b7cdd4",
        "danger":    "#b4232c",
        "ok":        "#176b47",
        "warning":   "#8a5a00",
        "activity":  "#086d91",
        "neutral":   "#5d7480",
        "error_bg": "#f7dfe2", "error_border": "#d98e96",
        "success_bg": "#dcefe6", "success_border": "#84bca1",
        "warning_bg": "#f4ead0", "warning_border": "#d1b66d",
        "activity_bg": "#d5edf3", "activity_border": "#82bfce",
        "neutral_bg": "#e1ebee", "neutral_border": "#afc2c8",
        "row_dirty_bg":  "#f4ead0",
        "row_dirty_fg":  "#795000",
        "row_sync_fg":   "#086d91",
        "row_norem_fg":  "#795000",
        "row_arch_fg":   "#71858e",
        "stripe":        "#edf6f8",
    },
}


def get_palette(name="dark"):
    return PALETTES.get(name, PALETTES["dark"])


def apply(root, name="dark"):
    """Apply palette to root + ttk style. Returns the palette dict."""
    pal = get_palette(name)
    root.configure(bg=pal["bg"])

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=pal["bg"], foreground=pal["text"],
                    bordercolor=pal["border"], lightcolor=pal["panel2"],
                    darkcolor=pal["panel"])
    style.configure("TFrame", background=pal["bg"])
    style.configure("Surface.TFrame", background=pal["panel"])
    style.configure("TLabelframe", background=pal["panel"],
                    bordercolor=pal["border"], relief="solid")
    style.configure("TLabelframe.Label", background=pal["panel"],
                    foreground=pal["accent2"], font=("", 9, "bold"))
    style.configure("TLabel", background=pal["bg"], foreground=pal["text"])
    style.configure("Surface.TLabel", background=pal["panel"],
                    foreground=pal["text"])
    style.configure("Muted.TLabel", background=pal["bg"],
                    foreground=pal["muted"])
    style.configure("SurfaceMuted.TLabel", background=pal["panel"],
                    foreground=pal["muted"])
    style.configure("Danger.TLabel", background=pal["bg"],
                    foreground=pal["danger"])

    style.configure("TButton", background=pal["panel2"],
                    foreground=pal["text"], bordercolor=pal["border"],
                    padding=(10, 4))
    style.map("TButton",
              background=[("active", pal["selection"]),
                          ("disabled", pal["panel"])],
              foreground=[("disabled", pal["muted"])])
    style.configure("Primary.TButton", background=pal["accent"],
                    foreground="#ffffff", bordercolor=pal["accent"],
                    font=("", 9, "bold"))
    style.map("Primary.TButton",
              background=[("active", pal["accent2"]),
                          ("disabled", pal["panel2"])],
              foreground=[("active", pal["header"]),
                          ("disabled", pal["muted"])])
    style.configure("Link.TButton", background=pal["panel"],
                    foreground=pal["accent2"], borderwidth=0,
                    padding=(4, 1))
    style.map("Link.TButton", foreground=[("active", pal["accent"])],
              background=[("active", pal["panel2"])])

    style.configure("TEntry", fieldbackground=pal["panel"],
                    foreground=pal["text"], insertcolor=pal["text"],
                    bordercolor=pal["border"], lightcolor=pal["panel"],
                    darkcolor=pal["panel"])
    style.map("TEntry", bordercolor=[("focus", pal["accent"])])

    style.configure("TCombobox", fieldbackground=pal["panel"],
                    foreground=pal["text"], arrowcolor=pal["text"],
                    bordercolor=pal["border"])
    style.map("TCombobox",
              fieldbackground=[("readonly", pal["panel"])],
              foreground=[("readonly", pal["text"])])
    root.option_add("*TCombobox*Listbox.background", pal["panel"])
    root.option_add("*TCombobox*Listbox.foreground", pal["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", pal["selection"])
    root.option_add("*TCombobox*Listbox.selectForeground",
                    pal["selection_fg"])

    style.configure("Treeview", background=pal["panel"],
                    fieldbackground=pal["panel"], foreground=pal["text"],
                    rowheight=26, bordercolor=pal["border"])
    style.map("Treeview",
              background=[("selected", pal["selection"])],
              foreground=[("selected", pal["selection_fg"])])
    style.configure("Treeview.Heading", background=pal["header"],
                    foreground=pal["accent2"], relief="flat",
                    padding=(6, 4), font=("", 9, "bold"))
    style.map("Treeview.Heading", background=[("active", pal["panel2"])])

    style.configure("TScrollbar", background=pal["panel2"],
                    troughcolor=pal["bg"], bordercolor=pal["bg"],
                    arrowcolor=pal["muted"])
    style.map("TScrollbar", background=[("active", pal["selection"])])

    style.configure("TPanedwindow", background=pal["bg"],
                    bordercolor=pal["bg"])
    style.configure("Panedwindow", background=pal["bg"])
    style.configure("TSpinbox", fieldbackground=pal["panel"],
                    foreground=pal["text"], arrowcolor=pal["text"],
                    bordercolor=pal["border"])
    style.configure("TCheckbutton", background=pal["bg"],
                    foreground=pal["text"])
    style.map("TCheckbutton", background=[("active", pal["bg"])])
    style.configure("Surface.TCheckbutton", background=pal["panel"],
                    foreground=pal["text"])
    style.map("Surface.TCheckbutton",
              background=[("active", pal["panel"])])

    style.configure("TNotebook", background=pal["bg"],
                    bordercolor=pal["border"])
    style.configure("TNotebook.Tab", background=pal["panel2"],
                    foreground=pal["muted"], padding=(12, 6))
    style.map("TNotebook.Tab",
              background=[("selected", pal["panel"]),
                          ("active", pal["selection"])],
              foreground=[("selected", pal["text"]),
                          ("active", pal["text"])])

    semantic = {
        "Error": (pal["danger"], pal["error_bg"], pal["error_border"]),
        "Success": (pal["ok"], pal["success_bg"], pal["success_border"]),
        "Warning": (pal["warning"], pal["warning_bg"], pal["warning_border"]),
        "Activity": (pal["activity"], pal["activity_bg"], pal["activity_border"]),
        "Neutral": (pal["neutral"], pal["neutral_bg"], pal["neutral_border"]),
    }
    for role, (fg, bg, border) in semantic.items():
        style.configure(f"Semantic.{role}.TLabel", background=bg,
                        foreground=fg, bordercolor=border, relief="solid",
                        borderwidth=1, padding=(7, 3),
                        font=("", 9, "bold"))
    return pal


def semantic_role(status):
    """Return Error/Success/Warning/Activity/Neutral for a known state."""
    key = str(status or "").strip().upper()
    if key in {"ERROR", "FAIL", "FAILED", "FAILED_TO_START",
               "CONTRADICTED", "NETWORK_FAILURE", "NOT_FOUND"}:
        return "Error"
    if key in {"PASS", "HEALTHY", "READY", "SUCCESS", "AVAILABLE", "VALID",
               "VERIFIED"}:
        return "Success"
    if key in {"WARN", "WARNING", "DIRTY", "TIMEOUT",
               "AUTHENTICATION_REQUIRED", "PARTIALLY_VERIFIED", "STALE",
               "INVALID_CONFIGURATION"}:
        return "Warning"
    if key in {"ACTIVE", "RUNNING", "STARTING", "STOPPING", "SCANNING", "BUSY",
               "SELECTED", "EDITING", "IN_PROGRESS"}:
        return "Activity"
    return "Neutral"


def semantic_style(status):
    """Return the ttk label style for a semantic state."""
    return f"Semantic.{semantic_role(status)}.TLabel"


def status_fg(pal, status):
    """Foreground colour token for a status/state word, defaulting to muted.

    Presentation-only: maps the known Health, Provider, Agent/run, and Project
    lifecycle statuses onto the existing palette tokens so states are scannable
    without inventing new colours or numeric scores. Unknown values fall back
    to the muted token.
    """
    key = str(status or "").strip().upper()
    mapping = {
        # Health overall status
        "PASS": pal["ok"],
        "WARN": pal["warning"],
        "FAIL": pal["danger"],
        "NOT_APPLICABLE": pal["muted"],
        "UNKNOWN": pal["muted"],
        "STALE": pal["row_arch_fg"],
        "UNAVAILABLE": pal["row_arch_fg"],
        # Provider observation states (all read-only)
        "AVAILABLE": pal["ok"],
        "AUTHENTICATION_REQUIRED": pal["danger"],
        "NETWORK_FAILURE": pal["danger"],
        "NOT_FOUND": pal["danger"],
        "TIMEOUT": pal["warning"],
        # Agent run / verification states
        "RUNNING": pal["accent2"],
        "STARTING": pal["accent"],
        "STOPPING": pal["accent"],
        "EXITED": pal["muted"],
        "TERMINATED": pal["row_arch_fg"],
        "FAILED_TO_START": pal["danger"],
        "FAILED": pal["danger"],
        "CONTRADICTED": pal["danger"],
        "VERIFIED": pal["ok"],
        "TARGET_RECHECKED": pal["neutral"],
        "PARTIALLY_VERIFIED": pal["warning"],
        "NOT_RUN": pal["muted"],
        "PENDING": pal["muted"],
        "NOT_STARTED": pal["muted"],
        # Project lifecycle status (curation values)
        "ACTIVE": pal["activity"],
        "PAUSED": pal["muted"],
        "ARCHIVED": pal["row_arch_fg"],
        "IDEA": pal["muted"],
    }
    return mapping.get(key, pal["muted"])


def style_tk_widget(w, pal, kind="text"):
    """Color plain-Tk widgets (Text, Listbox, Menu) manually."""
    if kind == "text":
        w.configure(bg=pal["panel"], fg=pal["text"],
                    insertbackground=pal["text"], relief="flat",
                    highlightthickness=1, highlightbackground=pal["border"],
                    highlightcolor=pal["accent"])
    elif kind == "list":
        w.configure(bg=pal["panel"], fg=pal["text"],
                    selectbackground=pal["selection"],
                    selectforeground=pal["selection_fg"], relief="flat",
                    highlightthickness=1, highlightbackground=pal["border"])
    elif kind == "menu":
        w.configure(bg=pal["panel"], fg=pal["text"], activebackground=pal["selection"],
                    activeforeground=pal["selection_fg"], bd=0,
                    relief="flat")
