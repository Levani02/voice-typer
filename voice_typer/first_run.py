"""The window that asks for the API keys.

A packaged executable has no `.env.example` to copy and no folder the user is expected to
poke around in, so the app has to ask for the things it cannot work out by itself. Asking
is also the only startup failure the app can repair: every other bad setting is a mistake
to report, while a missing key on a first run is simply a question nobody has been asked.

It asks for two, and they are not equal. **ElevenLabs is required** — without it there is
no dictation at all, which is why a first run cannot get past this window without one.
**Gemini is optional**: it is used by the rewrite mode and nowhere else, and without it
that mode pastes the raw transcript and says why on the card. So the second field may be
left empty and the app starts anyway.

The same window opens later from the card's menu, for the case this was written to fix:
somebody who installed months ago, has an ElevenLabs key already, and now wants the
rewrite mode. They should not have to be told to open `.env` in Notepad.

Keys go straight into `.env` beside the executable and into this process's environment.
They are never logged, never shown back, and never put in a window title.
"""

from __future__ import annotations

import logging
import tkinter as tk
import webbrowser

from voice_typer import widget_theme as theme
from voice_typer.config import ConfigError, has_gemini_key, save_api_key, save_gemini_key
from voice_typer.desktop_shortcut import create_desktop_shortcut

logger = logging.getLogger(__name__)

KEY_PAGE_URL = "https://elevenlabs.io/app/settings/api-keys"
GEMINI_KEY_PAGE_URL = "https://aistudio.google.com/apikey"

WINDOW_WIDTH = 540
# Tall enough for both fields and the buttons to sit inside the window rather than under
# its edge. Georgian wraps differently per machine, so this has slack in it on purpose.
WINDOW_HEIGHT = 560
PAD = 26

TITLE_TEXT = "voice-typer — გასაღებები"
HEADING_TEXT = "ერთი რამ დარჩა"
HEADING_TEXT_LATER = "გასაღებები"
EXPLANATION_TEXT = (
    "აპს ElevenLabs-ის გასაღები სჭირდება, რომ ხმა ტექსტად აქციოს.\n"
    "გასაღებები ამ კომპიუტერზე რჩება — არსად არ იგზავნება გარდა თვითონ ამ სერვისებისა."
)

ELEVEN_LABEL = "ElevenLabs-ის გასაღები — სავალდებულო"
ELEVEN_LINK = "გასაღების აღება: elevenlabs.io"
GEMINI_LABEL = "Gemini-ის გასაღები — სურვილისამებრ"
GEMINI_LINK = "გასაღების აღება: aistudio.google.com"
GEMINI_NOTE = (
    "მხოლოდ „გამართვის“ რეჟიმს სჭირდება. მის გარეშე კარნახი მუშაობს, "
    "გამართვა კი ნედლ ტექსტს სვამს და ბარათზე ამბობს რატომ."
)
KEPT_PLACEHOLDER = "ჩაწერილია — ცარიელი დატოვე, რომ არ შეიცვალოს"

EMPTY_KEY_MESSAGE = "ElevenLabs-ის ველი ცარიელია — ჩასვი გასაღები და თავიდან სცადე."
SAVE_FAILED_MESSAGE = "გასაღები ვერ შეინახა: {reason}"
SAVE_TEXT = "შენახვა და გაშვება"
SAVE_TEXT_LATER = "შენახვა"
SHORTCUT_TEXT = "დესკტოპზე ხატულის დადება"


class KeysWindow:
    """A small modal window. `run` blocks until it is answered or closed.

    Two shapes, one class. On a first run it makes its own Tk root, insists on an
    ElevenLabs key, and offers a desktop shortcut. Opened later from the card's menu it
    is a `Toplevel` on the window that already exists — Tk allows exactly one root — and
    an empty field means "leave that one alone" rather than "there isn't one".
    """

    def __init__(self, master: tk.Misc | None = None) -> None:
        self._saved = False
        self._later = master is not None
        self._own_root = master is None
        self._root: tk.Misc = tk.Toplevel(master) if master is not None else tk.Tk()
        self._build()

    # ------------------------------------------------------------------------- building

    def _build(self) -> None:
        self._root.title(TITLE_TEXT)
        self._root.configure(bg=theme.CARD_TOP)
        self._root.resizable(False, False)
        self._centre()
        self._root.protocol("WM_DELETE_WINDOW", self._root.destroy)

        self._add_heading()
        self._add_eleven_row()
        self._add_gemini_row()
        self._add_message()
        if not self._later:
            self._add_shortcut_choice()
        self._add_buttons()

        self._root.bind("<Return>", lambda _event: self._save())
        self._root.bind("<Escape>", lambda _event: self._root.destroy())
        self._come_to_the_front()

    def _come_to_the_front(self) -> None:
        """A setup window the user cannot find is the same as no window at all.

        Started from a shortcut the app has no foreground claim, so the window can open
        behind whatever was already on screen. It is raised once and then released: a
        window that stays permanently on top would be in the way while a key is being
        copied out of a browser.
        """
        self._root.attributes("-topmost", True)
        self._root.lift()
        self._root.after(400, lambda: self._root.attributes("-topmost", False))
        try:
            self._root.focus_force()
        except tk.TclError as exc:
            logger.debug("could not take the focus: %s", exc)
        # Opened from the menu the ElevenLabs key is already there, so the cursor starts
        # in the field the user actually came to fill in.
        (self._gemini_entry if self._later else self._eleven_entry).focus_set()

    def _centre(self) -> None:
        x = (self._root.winfo_screenwidth() - WINDOW_WIDTH) // 2
        y = (self._root.winfo_screenheight() - WINDOW_HEIGHT) // 3
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def _add_heading(self) -> None:
        tk.Label(
            self._root,
            text=HEADING_TEXT_LATER if self._later else HEADING_TEXT,
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

    def _add_link(self, text: str, url: str) -> None:
        link = tk.Label(
            self._root,
            text=text,
            bg=theme.CARD_TOP,
            fg=theme.ACCENT,
            font=(theme.UI_FAMILY, 9, "underline"),
            cursor="hand2",
        )
        link.pack(anchor="w", padx=PAD, pady=(4, 0))
        link.bind("<Button-1>", lambda _event, where=url: self._open_page(where))

    def _add_field_label(self, text: str) -> None:
        tk.Label(
            self._root,
            text=text,
            bg=theme.CARD_TOP,
            fg=theme.TEXT_MUTED,
            font=(theme.UI_FAMILY, 10),
        ).pack(anchor="w", padx=PAD, pady=(16, 4))

    def _add_entry(self) -> tk.Entry:
        entry = tk.Entry(
            self._root,
            show="•",  # typed and pasted keys stay invisible
            bg="#1a1d20",
            fg=theme.TEXT_BRIGHT,
            insertbackground=theme.TEXT_BRIGHT,
            relief="flat",
            font=(theme.MONO_FAMILY, 11),
        )
        entry.pack(fill="x", padx=PAD, ipady=6)
        return entry

    def _add_eleven_row(self) -> None:
        self._add_field_label(ELEVEN_LABEL)
        self._eleven_entry = self._add_entry()
        if self._later:
            tk.Label(
                self._root,
                text=KEPT_PLACEHOLDER,
                bg=theme.CARD_TOP,
                fg=theme.TEXT_FAINT,
                font=(theme.UI_FAMILY, 9),
            ).pack(anchor="w", padx=PAD, pady=(4, 0))
        self._add_link(ELEVEN_LINK, KEY_PAGE_URL)

    def _add_gemini_row(self) -> None:
        self._add_field_label(GEMINI_LABEL)
        self._gemini_entry = self._add_entry()
        tk.Label(
            self._root,
            text=KEPT_PLACEHOLDER if (self._later and has_gemini_key()) else GEMINI_NOTE,
            bg=theme.CARD_TOP,
            fg=theme.TEXT_FAINT,
            font=(theme.UI_FAMILY, 9),
            justify="left",
            wraplength=WINDOW_WIDTH - 2 * PAD,
        ).pack(anchor="w", padx=PAD, pady=(4, 0))
        self._add_link(GEMINI_LINK, GEMINI_KEY_PAGE_URL)

    def _add_message(self) -> None:
        self._message = tk.Label(
            self._root,
            text="",
            bg=theme.CARD_TOP,
            fg="#f06565",
            font=(theme.UI_FAMILY, 9),
            wraplength=WINDOW_WIDTH - 2 * PAD,
            justify="left",
        )
        self._message.pack(anchor="w", padx=PAD, pady=(10, 0))

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
            text=SAVE_TEXT_LATER if self._later else SAVE_TEXT,
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
            text="დახურვა" if self._later else "გამოსვლა",
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

    def _open_page(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception as exc:
            logger.warning("could not open the key page: %s", exc)
            self._message.config(text=url)

    def _save(self) -> None:
        """Save whichever fields were filled in. An empty field changes nothing.

        On a first run the ElevenLabs field cannot be empty — there is no key to keep.
        Opened later it can be, and then only the Gemini one is written.
        """
        eleven = self._eleven_entry.get().strip()
        gemini = self._gemini_entry.get().strip()

        if not eleven and not self._later:
            self._message.config(text=EMPTY_KEY_MESSAGE)
            return

        try:
            if eleven:
                save_api_key(eleven)
            if gemini:
                save_gemini_key(gemini)
        except (ConfigError, OSError) as exc:
            # The exception type, never the value — an error message is a place a key has
            # escaped into a log file before.
            logger.error("could not save a key: %s", type(exc).__name__)
            self._message.config(text=SAVE_FAILED_MESSAGE.format(reason=exc))
            return

        logger.info(
            "keys saved from the %s window: elevenlabs=%s gemini=%s",
            "settings" if self._later else "first-run",
            bool(eleven),
            bool(gemini),
        )

        if not self._later and self._wants_shortcut.get():
            where = create_desktop_shortcut()
            logger.info("desktop shortcut: %s", where or "not created")

        self._saved = True
        self._root.destroy()

    # ------------------------------------------------------------------------ lifecycle

    def run(self) -> bool:
        """True once something has been saved, False if the user closed the window."""
        if self._own_root:
            self._root.mainloop()
        else:
            # A Toplevel has no loop of its own; the card's root is already running one.
            self._root.grab_set()
            self._root.wait_window()
        return self._saved


def ask_for_api_key() -> bool:
    """The first run. False means the user chose to leave instead of entering a key."""
    try:
        return KeysWindow().run()
    except Exception:
        logger.exception("the first-run window could not be shown")
        return False


def ask_for_keys(master: tk.Misc) -> bool:
    """The same window, opened later from the card's menu, on the root that exists.

    True when something was saved, so the caller knows to pick the new key up.
    """
    try:
        return KeysWindow(master).run()
    except Exception:
        logger.exception("the keys window could not be shown")
        return False
