"""The window that asks for the ElevenLabs key, once, on the very first run.

A packaged executable has no `.env.example` to copy and no folder the user is expected to
poke around in, so the app has to ask for the one thing it cannot work without. Asking is
also the only failure the app can repair by itself: every other bad setting is a mistake
to report, while a missing key on a first run is simply a question nobody has been asked.

The key goes straight into `.env` next to the executable and into this process's
environment. It is never logged, never shown back, and never put in a window title.
"""

from __future__ import annotations

import logging
import tkinter as tk
import webbrowser

from voice_typer import widget_theme as theme
from voice_typer.config import ConfigError, save_api_key
from voice_typer.desktop_shortcut import create_desktop_shortcut

logger = logging.getLogger(__name__)

KEY_PAGE_URL = "https://elevenlabs.io/app/settings/api-keys"

WINDOW_WIDTH = 540
# Tall enough for the buttons to sit inside the window rather than under its edge.
WINDOW_HEIGHT = 440
PAD = 26

TITLE_TEXT = "voice-typer — პირველი გაშვება"
HEADING_TEXT = "ერთი რამ დარჩა"
EXPLANATION_TEXT = (
    "აპს ElevenLabs-ის გასაღები სჭირდება, რომ ხმა ტექსტად აქციოს.\n"
    "გასაღები ამ კომპიუტერზე რჩება — არსად არ იგზავნება გარდა ElevenLabs-ისა."
)
EMPTY_KEY_MESSAGE = "ველი ცარიელია — ჩასვი გასაღები და თავიდან სცადე."
SAVE_FAILED_MESSAGE = "გასაღები ვერ შეინახა: {reason}"
SHORTCUT_TEXT = "დესკტოპზე ხატულის დადება"


class FirstRunWindow:
    """A small modal window. `run` blocks until it is answered or closed."""

    def __init__(self) -> None:
        self._saved = False
        self._root = tk.Tk()
        self._build()

    # ------------------------------------------------------------------------- building

    def _build(self) -> None:
        self._root.title(TITLE_TEXT)
        self._root.configure(bg=theme.CARD_TOP)
        self._root.resizable(False, False)
        self._centre()
        self._root.protocol("WM_DELETE_WINDOW", self._root.destroy)

        self._add_heading()
        self._add_key_row()
        self._add_shortcut_choice()
        self._add_buttons()

        self._root.bind("<Return>", lambda _event: self._save())
        self._root.bind("<Escape>", lambda _event: self._root.destroy())
        self._come_to_the_front()

    def _come_to_the_front(self) -> None:
        """A setup window the user cannot find is the same as no window at all.

        Started from a shortcut the app has no foreground claim, so the window can open
        behind whatever was already on screen. It is raised once and then released: a
        window that stays permanently on top would be in the way while the key is being
        copied out of a browser.
        """
        self._root.attributes("-topmost", True)
        self._root.lift()
        self._root.after(400, lambda: self._root.attributes("-topmost", False))
        try:
            self._root.focus_force()
        except tk.TclError as exc:
            logger.debug("could not take the focus: %s", exc)
        self._entry.focus_set()

    def _centre(self) -> None:
        x = (self._root.winfo_screenwidth() - WINDOW_WIDTH) // 2
        y = (self._root.winfo_screenheight() - WINDOW_HEIGHT) // 3
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def _add_heading(self) -> None:
        tk.Label(
            self._root,
            text=HEADING_TEXT,
            bg=theme.CARD_TOP,
            fg=theme.TEXT_BRIGHT,
            font=(theme.UI_FAMILY, 16, "bold"),
        ).pack(anchor="w", padx=PAD, pady=(PAD, 6))

        tk.Label(
            self._root,
            text=EXPLANATION_TEXT,
            bg=theme.CARD_TOP,
            fg=theme.TEXT_MUTED,
            font=(theme.UI_FAMILY, 10),
            justify="left",
            # Wrapped by width rather than by hand: the Georgian line length depends on
            # the font the system picked, so a hard-coded break lands in a different
            # place on every machine.
            wraplength=WINDOW_WIDTH - 2 * PAD,
        ).pack(anchor="w", padx=PAD)

        link = tk.Label(
            self._root,
            text="გასაღების აღება: elevenlabs.io",
            bg=theme.CARD_TOP,
            fg=theme.ACCENT,
            font=(theme.UI_FAMILY, 10, "underline"),
            cursor="hand2",
        )
        link.pack(anchor="w", padx=PAD, pady=(10, 0))
        link.bind("<Button-1>", lambda _event: self._open_key_page())

    def _add_key_row(self) -> None:
        tk.Label(
            self._root,
            text="გასაღები",
            bg=theme.CARD_TOP,
            fg=theme.TEXT_MUTED,
            font=(theme.UI_FAMILY, 10),
        ).pack(anchor="w", padx=PAD, pady=(18, 4))

        self._entry = tk.Entry(
            self._root,
            show="•",  # typed and pasted keys stay invisible
            bg="#1a1d20",
            fg=theme.TEXT_BRIGHT,
            insertbackground=theme.TEXT_BRIGHT,
            relief="flat",
            font=(theme.MONO_FAMILY, 11),
        )
        self._entry.pack(fill="x", padx=PAD, ipady=6)

        self._message = tk.Label(
            self._root,
            text="",
            bg=theme.CARD_TOP,
            fg="#f06565",
            font=(theme.UI_FAMILY, 9),
            wraplength=WINDOW_WIDTH - 2 * PAD,
            justify="left",
        )
        self._message.pack(anchor="w", padx=PAD, pady=(8, 0))

    def _add_shortcut_choice(self) -> None:
        """Offered rather than done silently.

        A downloaded executable sits in whatever folder the browser chose, so without an
        icon the second run means remembering where that was. Putting one there uninvited
        is still somebody else's desktop, so it is a choice — just one that starts ticked.
        """
        self._wants_shortcut = tk.BooleanVar(value=True)
        tk.Checkbutton(
            self._root,
            text=SHORTCUT_TEXT,
            variable=self._wants_shortcut,
            bg=theme.CARD_TOP,
            fg=theme.TEXT_MUTED,
            activebackground=theme.CARD_TOP,
            activeforeground=theme.TEXT_BRIGHT,
            selectcolor="#1a1d20",
            highlightthickness=0,
            borderwidth=0,
            font=(theme.UI_FAMILY, 10),
        ).pack(anchor="w", padx=PAD - 2, pady=(14, 0))

    def _add_buttons(self) -> None:
        row = tk.Frame(self._root, bg=theme.CARD_TOP)
        row.pack(fill="x", padx=PAD, pady=(14, PAD), side="bottom")

        tk.Button(
            row,
            text="შენახვა და გაშვება",
            command=self._save,
            bg=theme.BUTTON_TOP,
            fg=theme.TEXT_BRIGHT,
            activebackground=theme.BUTTON_TOP_HOVER,
            activeforeground=theme.TEXT_BRIGHT,
            relief="flat",
            font=(theme.UI_FAMILY, 10),
            padx=16,
            pady=6,
        ).pack(side="left")

        tk.Button(
            row,
            text="გამოსვლა",
            command=self._root.destroy,
            bg=theme.CARD_BOTTOM,
            fg=theme.TEXT_MUTED,
            activebackground=theme.BUTTON_TOP,
            activeforeground=theme.TEXT_BRIGHT,
            relief="flat",
            font=(theme.UI_FAMILY, 10),
            padx=16,
            pady=6,
        ).pack(side="right")

    # -------------------------------------------------------------------------- actions

    def _open_key_page(self) -> None:
        try:
            webbrowser.open(KEY_PAGE_URL)
        except Exception as exc:
            logger.warning("could not open the key page: %s", exc)
            self._message.config(text=KEY_PAGE_URL)

    def _save(self) -> None:
        key = self._entry.get().strip()
        if not key:
            self._message.config(text=EMPTY_KEY_MESSAGE)
            return

        try:
            save_api_key(key)
        except (ConfigError, OSError) as exc:
            logger.error("could not save the key: %s", type(exc).__name__)
            self._message.config(text=SAVE_FAILED_MESSAGE.format(reason=exc))
            return

        if self._wants_shortcut.get():
            where = create_desktop_shortcut()
            logger.info("desktop shortcut: %s", where or "not created")

        self._saved = True
        self._root.destroy()

    # ------------------------------------------------------------------------ lifecycle

    def run(self) -> bool:
        """True once a key has been saved, False if the user closed the window."""
        self._root.mainloop()
        return self._saved


def ask_for_api_key() -> bool:
    """Show the first-run window. False means the user chose to leave instead."""
    try:
        return FirstRunWindow().run()
    except Exception:
        logger.exception("the first-run window could not be shown")
        return False
