"""Make tkinter work from inside a virtual environment on Windows.

Kept in its own module because two files need it and neither is the other's owner:
`overlay.py` creates the recorder window, and `first_run.py` creates its own root when
there is no window yet to hang off. Before this module the second one worked only because
`main.py` happens to import the overlay first — a load-bearing import order that nothing
stated and nothing enforced.

The fix must run before `tk.Tk()`, not before `import tkinter`. Importing tkinter reads
no paths; it is creating a window that goes looking for `init.tcl`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def point_at_the_base_installation() -> None:
    """Point Tcl and Tk at the real Python installation's runtime.

    A venv copies python.exe but not the Tcl runtime, and the search path tkinter builds
    from `sys.prefix` looks for `lib/tcl8.6` — while the real Python installs it under
    `tcl/tcl8.6`. The result is `TclError: Can't find a usable init.tcl` the moment a
    window is created, which under pythonw.exe means the app dies with no message at all.

    macOS keeps Tcl where tkinter expects it, so the directory this looks for is absent
    and the function does nothing there. An environment variable somebody set on purpose
    is always left alone.
    """
    tcl_root = Path(sys.base_prefix) / "tcl"
    if not tcl_root.is_dir():
        return

    for variable, prefix in (("TCL_LIBRARY", "tcl"), ("TK_LIBRARY", "tk")):
        if os.environ.get(variable):
            continue
        candidates = sorted(tcl_root.glob(f"{prefix}[0-9]*.[0-9]*"), reverse=True)
        if candidates:
            os.environ[variable] = str(candidates[0])
