"""The tray icon — the app's entire visible surface.

At 16x16 the shape carries no information, so colour does all the work: grey means ready,
red means recording, amber means the audio is being transcribed, dark red means something
failed and the reason is in the log.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum

import pystray
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

ICON_SIZE = 64
APP_NAME = "voice-typer"


class TrayState(Enum):
    """Colour and tooltip for each thing the app can be doing."""

    IDLE = ("#8a8a8a", "მზადაა — დააჭირე ღილაკს და ილაპარაკე")
    RECORDING = ("#d92d20", "იწერს...")
    TRANSCRIBING = ("#f5a623", "ტექსტად გარდაქმნა...")
    ERROR = ("#7a1512", "შეცდომა — იხილე logs\\voice_typer.log")

    def __init__(self, colour: str, tooltip: str) -> None:
        self.colour = colour
        self.tooltip = tooltip


def _make_icon_image(colour: str) -> Image.Image:
    """A filled circle in the state colour, with a small margin so it reads cleanly."""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = ICON_SIZE // 8
    draw.ellipse((margin, margin, ICON_SIZE - margin, ICON_SIZE - margin), fill=colour)
    return image


class TrayIcon:
    """Wraps pystray. `run` blocks, so it owns the main thread."""

    def __init__(
        self,
        *,
        on_retry: Callable[[], None],
        on_open_logs: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
        usage_text: Callable[[], str],
    ) -> None:
        self._usage_text = usage_text
        self._state = TrayState.IDLE
        self._icon = pystray.Icon(
            APP_NAME,
            icon=_make_icon_image(TrayState.IDLE.colour),
            title=TrayState.IDLE.tooltip,
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: self._usage_text(), None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("ბოლო ჩანაწერის ხელახლა გაგზავნა", lambda: on_retry()),
                pystray.MenuItem("ლოგების საქაღალდე", lambda: on_open_logs()),
                pystray.MenuItem("პარამეტრები (config.json)", lambda: on_open_settings()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("გამორთვა", lambda: on_quit()),
            ),
        )

    def run(self) -> None:
        """Show the icon and block until `stop` is called."""
        self._icon.run()

    def stop(self) -> None:
        self._icon.stop()

    def set_state(self, state: TrayState) -> None:
        """Repaint the icon. Safe to call from any thread."""
        if state is self._state:
            return
        self._state = state
        try:
            self._icon.icon = _make_icon_image(state.colour)
            self._icon.title = state.tooltip
        except Exception as exc:  # a repaint failure must never take the app down
            logger.warning("could not update the tray icon: %s", exc)

    def notify(self, message: str, title: str = APP_NAME) -> None:
        """Show a desktop notification. Never contains a key or a transcript."""
        try:
            self._icon.notify(message, title)
        except Exception as exc:
            logger.warning("could not show a notification (%s): %s", message, exc)
