"""The card's right-click menu — everything the hidden tray icon used to offer.

Split out of `overlay.py` on the same principle as `card_glyphs.py`: this file knows how
to build a menu and how to relabel it, and nothing about what state the app is in. What
each entry should say is decided by the caller and handed over as plain values.

Building and showing are deliberately separate. `tk_popup` enters the system's own modal
loop and does not return until the menu is dismissed, which nothing can do in a test —
so `sync` is reachable on its own, and it is `sync` that carries the labelling rules.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from voice_typer import widget_theme as theme

# Only the entries whose label changes are remembered by name. Counting positions by hand
# is how a menu ends up relabelling the wrong line the next time somebody inserts an item.
Index = dict[str, int]

TICK = "✓ "
LISTENING = "F9-ის მოსმენა"
REWRITE_MODE = "გამართვის რეჟიმი"
FOLD = "ჩაკეცვა"
UNFOLD = "გაშლა"


class Actions:
    """What the menu can ask for. Assembled by the window from itself and the app."""

    def __init__(
        self,
        *,
        toggle_enabled: Callable[[], None],
        retry_last: Callable[[], None],
        copy_raw_text: Callable[[], None],
        toggle_rewrite_mode: Callable[[], None],
        toggle_collapsed: Callable[[], None],
        open_logs: Callable[[], None],
        open_settings: Callable[[], None],
        open_rewrite_prompt: Callable[[], None],
        open_keys: Callable[[], None],
        quit_app: Callable[[], None],
    ) -> None:
        self.toggle_enabled = toggle_enabled
        self.retry_last = retry_last
        self.copy_raw_text = copy_raw_text
        self.toggle_rewrite_mode = toggle_rewrite_mode
        self.toggle_collapsed = toggle_collapsed
        self.open_logs = open_logs
        self.open_settings = open_settings
        self.open_rewrite_prompt = open_rewrite_prompt
        self.open_keys = open_keys
        self.quit_app = quit_app


def build(root: tk.Misc, actions: Actions) -> tuple[tk.Menu, Index]:
    """The menu, and where each relabelling entry landed in it."""
    menu = tk.Menu(
        root,
        tearoff=0,
        bg="#26292c",
        fg=theme.TEXT_BRIGHT,
        activebackground="#3a3e43",
        activeforeground=theme.TEXT_BRIGHT,
        borderwidth=0,
    )
    index: Index = {}

    def remembered(name: str, **options) -> None:
        menu.add_command(**options)
        index[name] = menu.index("end")

    remembered("usage", label="", state="disabled")  # filled in on open
    menu.add_separator()
    remembered("listening", label=LISTENING, command=actions.toggle_enabled)
    menu.add_command(label="ბოლო ჩანაწერის ხელახლა გაგზავნა", command=actions.retry_last)
    menu.add_command(label="ნედლი ტექსტი clipboard-ში", command=actions.copy_raw_text)
    remembered("mode", label=REWRITE_MODE, command=actions.toggle_rewrite_mode)
    remembered("fold", label=FOLD, command=actions.toggle_collapsed)
    menu.add_separator()
    menu.add_command(label="ლოგების საქაღალდე", command=actions.open_logs)
    menu.add_command(label="პარამეტრები (config.json)", command=actions.open_settings)
    menu.add_command(
        label="გამართვის ინსტრუქცია (rewrite-prompt.md)", command=actions.open_rewrite_prompt
    )
    menu.add_command(label="API გასაღებები…", command=actions.open_keys)
    menu.add_separator()
    menu.add_command(label="გამორთვა", command=actions.quit_app)
    return menu, index


def sync(
    menu: tk.Menu,
    index: Index,
    *,
    usage: str,
    listening: bool,
    rewrite: bool,
    folded: bool,
) -> None:
    """Bring the menu's live entries up to date, without showing it."""
    menu.entryconfig(index["usage"], label=usage)
    menu.entryconfig(index["listening"], label=_ticked(LISTENING, listening))
    menu.entryconfig(index["mode"], label=_ticked(REWRITE_MODE, rewrite))
    menu.entryconfig(index["fold"], label=(UNFOLD if folded else FOLD))


def _ticked(label: str, on: bool) -> str:
    return f"{TICK}{label}" if on else label


def popup(menu: tk.Menu, x: int, y: int) -> None:
    """Show the menu at a screen position and always let go of the pointer afterwards."""
    try:
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


def open_keys(root: tk.Misc, on_saved: Callable[[], None]) -> None:
    """The keys window, as a child of the card.

    Opened from here rather than from `app.py` because Tk allows exactly one root and the
    window owns it — a second `tk.Tk()` from the state machine would be a second event
    loop and a hung window. The app is told afterwards, so a key typed in just now takes
    effect on the next dictation rather than after a restart.

    Imported inside the function: this is a rare path and the module pulls in the whole
    first-run screen, which nothing else here needs.
    """
    from voice_typer.first_run import ask_for_keys

    if ask_for_keys(root):
        on_saved()
