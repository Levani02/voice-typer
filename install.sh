#!/usr/bin/env bash
#
# Sets up voice-typer on macOS: virtual environment, dependencies, API key, launcher.
#
# Run this once after downloading the project:
#
#     bash install.sh
#
# Options:
#   --non-interactive   never ask anything; the API key step is skipped
#   --skip-launcher     do not create run.command
#
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="$PROJECT_ROOT/.env"
LAUNCHER="$PROJECT_ROOT/run.command"

# The only version the project is built and tested against. 3.14 is deliberately absent:
# sounddevice and pynput do not publish wheels for it yet.
PYTHON_VERSION="3.13"

# The one setting the user has to supply. Named once, used everywhere below.
KEY_SETTING="ELEVENLABS_API_KEY"

NON_INTERACTIVE=0
SKIP_LAUNCHER=0
for argument in "$@"; do
    case "$argument" in
        --non-interactive) NON_INTERACTIVE=1 ;;
        --skip-launcher) SKIP_LAUNCHER=1 ;;
        *) echo "Unknown option: $argument" >&2; exit 2 ;;
    esac
done

CYAN=$'\033[36m'
GREEN=$'\033[32m'
GREY=$'\033[90m'
RED=$'\033[31m'
PLAIN=$'\033[0m'

step()    { printf '\n%s[%s/6] %s%s\n' "$CYAN" "$1" "$2" "$PLAIN"; }
ok()      { printf '      %s%s%s\n' "$GREEN" "$1" "$PLAIN"; }
note()    { printf '      %s%s%s\n' "$GREY" "$1" "$PLAIN"; }
problem() { printf '\n%s%s%s\n' "$RED" "$1" "$PLAIN"; }

stop_with_message() {
    problem "$1"
    echo
    exit 1
}

# ---------------------------------------------------------------------------- 1. Python

find_python() {
    # Homebrew and the python.org installer put it in different places, and neither is
    # guaranteed to be on PATH when this runs from a double-clicked file.
    local candidate
    for candidate in \
        "$(command -v "python$PYTHON_VERSION" 2>/dev/null || true)" \
        "/opt/homebrew/bin/python$PYTHON_VERSION" \
        "/usr/local/bin/python$PYTHON_VERSION" \
        "/Library/Frameworks/Python.framework/Versions/$PYTHON_VERSION/bin/python$PYTHON_VERSION"
    do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    # A plain `python3` will do if it happens to be the right version.
    candidate="$(command -v python3 2>/dev/null || true)"
    if [ -n "$candidate" ]; then
        local found
        found="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
        if [ "$found" = "$PYTHON_VERSION" ]; then
            echo "$candidate"
            return 0
        fi
    fi
    return 1
}

echo "voice-typer — setup"
echo "==================="

if [ "$(uname -s)" != "Darwin" ]; then
    stop_with_message "This installer is for macOS. On Windows, run install.ps1 instead."
fi

step 1 "Looking for Python"
if ! PYTHON="$(find_python)"; then
    stop_with_message "No suitable Python was found.

voice-typer needs Python $PYTHON_VERSION.
Python 3.14 does not work yet — the audio and keyboard libraries have no build for it.

Install it with Homebrew:

    brew install python@$PYTHON_VERSION python-tk@$PYTHON_VERSION

or download it from https://www.python.org/downloads/
Then run this file again."
fi
ok "Python $PYTHON_VERSION found"

# ------------------------------------------------------------------- 2. the environment

step 2 "Creating the private environment for this app"
if [ -x "$VENV_PYTHON" ]; then
    note "Already there — reusing it."
else
    if ! "$PYTHON" -m venv "$PROJECT_ROOT/.venv"; then
        stop_with_message "Could not create the environment (.venv)."
    fi
    ok "Created .venv"
fi
if [ ! -x "$VENV_PYTHON" ]; then
    stop_with_message "The environment was created but has no python in it."
fi

# ------------------------------------------------------------------ 3. the dependencies

step 3 "Installing the software the app needs (this takes a minute or two)"
"$VENV_PYTHON" -m pip install --upgrade pip --quiet --disable-pip-version-check
if ! "$VENV_PYTHON" -m pip install -r "$PROJECT_ROOT/requirements.txt" --quiet --disable-pip-version-check; then
    stop_with_message "Installing the dependencies failed.

The usual cause is no internet connection, or a network blocking pypi.org.
Nothing has been broken — run this file again once the connection is back."
fi
ok "All dependencies installed"

# ---------------------------------------------------------------------- 4. the API key

key_is_filled_in() {
    # An .env with an empty key line is not configured — treat it as if it were absent,
    # or a half-finished first run would silently skip the question for ever after.
    [ -f "$ENV_FILE" ] || return 1
    grep -Eq "^[[:space:]]*$KEY_SETTING[[:space:]]*=[[:space:]]*[^[:space:]]" "$ENV_FILE"
}

save_api_key() {
    # Written straight to .env, which is in .gitignore and never leaves this machine.
    # The setting name is a variable so that nothing in this repository ever contains
    # the name and a value side by side — that is what a leaked key looks like.
    local key="$1"
    local name="$KEY_SETTING"
    awk -v name="$name" -v value="$key" '
        index($0, name "=") == 1 { print name "=" value; next }
        { print }
    ' "$PROJECT_ROOT/.env.example" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
}

step 4 "Setting up your ElevenLabs API key"
if key_is_filled_in; then
    note "Your key is already in .env — leaving it alone."
elif [ "$NON_INTERACTIVE" = "1" ]; then
    [ -f "$ENV_FILE" ] || cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
    note "Created .env — put your key in it before starting the app."
else
    echo
    echo "      Paste your ElevenLabs API key and press Enter."
    printf '      %sGet one at: https://elevenlabs.io/app/settings/api-keys%s\n' "$GREY" "$PLAIN"
    printf '      %sNothing appears as you type or paste — that is deliberate.%s\n' "$GREY" "$PLAIN"
    echo
    printf '      API key: '
    read -rs API_KEY
    echo
    API_KEY="$(printf '%s' "$API_KEY" | tr -d '[:space:]')"
    if [ -z "$API_KEY" ]; then
        [ -f "$ENV_FILE" ] || cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
        note "No key entered. Created .env — open it and paste the key in later."
    else
        save_api_key "$API_KEY"
        ok "Key saved to .env (${#API_KEY} characters)"
        unset API_KEY
    fi
fi

# --------------------------------------------------------------------- 5. verification

step 5 "Checking that everything loads"
CHECK_OUTPUT="$("$VENV_PYTHON" -c 'import elevenlabs, sounddevice, pynput, pystray, pyperclip, tkinter; print("ok")' 2>&1)"
if [ "$CHECK_OUTPUT" != "ok" ]; then
    if printf '%s' "$CHECK_OUTPUT" | grep -q "_tkinter"; then
        stop_with_message "Python on this Mac has no window toolkit, so the recorder window cannot be drawn.

Install it with:

    brew install python-tk@$PYTHON_VERSION

then delete the .venv folder and run this file again."
    fi
    stop_with_message "The app's parts installed but do not load:

$CHECK_OUTPUT"
fi
ok "Everything loads"

# ------------------------------------------------------------------------- 6. launcher

step 6 "Creating the launcher"
if [ "$SKIP_LAUNCHER" = "1" ]; then
    note "Skipped, as asked."
else
    cat > "$LAUNCHER" <<'LAUNCHER_EOF'
#!/usr/bin/env bash
# Double-click this file to start voice-typer.
#
# Terminal opens behind the app and can be closed straight away — the recorder window
# is the app itself.
cd "$(dirname "$0")" || exit 1
exec ./.venv/bin/python main.py
LAUNCHER_EOF
    chmod +x "$LAUNCHER"
    ok "Created run.command — double-click it to start the app"

    # A symlink on the Desktop, so starting the app does not mean finding this folder
    # again. Finder opens it exactly as if it were the launcher itself.
    if [ -d "$HOME/Desktop" ]; then
        ln -sf "$LAUNCHER" "$HOME/Desktop/voice-typer.command"
        ok "Put a voice-typer icon on your desktop"
    fi
fi

# ----------------------------------------------------------------------------- finish

cat <<'FINISH_EOF'

Done. voice-typer is installed.

To start it:      double-click run.command in this folder
To use it:        hold F9, speak Georgian, let go — the text appears
                  wherever your cursor is

One more thing — macOS will not let any app watch the keyboard or paste for you
until you allow it:

  1. Start the app once. macOS shows a permission request.
  2. Open  System Settings > Privacy & Security > Accessibility
     and switch on the entry for Terminal (or for Python).
  3. Do the same under  Privacy & Security > Microphone.
  4. Quit the app and start it again.

Without step 2 the F9 key does nothing and no text is ever pasted.
Without step 3 the recording is silent.

If something goes wrong, the reason is written in logs/voice_typer.log
FINISH_EOF
