"""Keeping the browser window behind the operator's work.

A headed run opens a real window, and Chromium activates itself as it opens, so on
macOS the window lands on top of whatever the operator was doing — every run, even
the fifth one that only adds a page to an existing collection. ``--keep-background``
gives the screen back to the work that had it: the frontmost application is noted
before the browser opens, and a small watcher hands the screen back whenever the
browser takes it, for a few seconds after the launch and no longer. Nothing is
forced after that — the window is open and one click away, it just never jumps in
front uninvited. The same choice keeps a challenge from raising the window; the
bell and the takeover notice still tell the human it is their turn.

The screen can only be read on macOS, through System Events, which asks for
automation permission the first time. Elsewhere the flag is accepted and changes
nothing: which window is on top is the window manager's business there.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

BUDGET_SECONDS = 8.0
"""How long the watcher looks for the browser to take the screen after the launch."""

POLL_SECONDS = 0.3
"""How often the watcher reads which application is frontmost."""

GIVE_BACKS = 3
"""How many times the screen may be handed back before the watcher stops trying."""

APP_NAMES = {
    "chrome": "Google Chrome",
    "chrome-beta": "Google Chrome Beta",
    "chrome-canary": "Google Chrome Canary",
    "chrome-dev": "Google Chrome Dev",
    "msedge": "Microsoft Edge",
    "msedge-beta": "Microsoft Edge Beta",
    "msedge-dev": "Microsoft Edge Dev",
}
"""The macOS application process name for each ``--channel`` value.

Chromium itself, the browser a run gets when ``--channel`` is empty, is not in the
table: it is the fallback below, which is also what an unknown channel gets.
"""

FALLBACK_APP_NAME = "Chromium"
"""Process name of the bundled browser, and the guess for a channel the table lacks."""


def app_name(channel: str | None) -> str:
    """Name the application process this run's browser opens as.

    :param channel: the ``--channel`` value, or None for the bundled Chromium.
    :returns: the name macOS shows for that browser's application process.
    """
    return APP_NAMES.get(channel or "", FALLBACK_APP_NAME)


def _osascript(script: str) -> str | None:
    """Run one AppleScript and return its trimmed output.

    :param script: the AppleScript to run, which reads or sets the frontmost process.
    :returns: the script's output, or None when macOS refused — another platform's
        ``osascript`` is absent, or automation permission was declined.
    """
    try:
        result = subprocess.run(  # fixed binary, no shell, script built here
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


_FRONTMOST_SCRIPT = (
    'tell application "System Events" to get name of first application process '
    "whose frontmost is true"
)


def frontmost_app() -> str | None:
    """Name the application process that owns the screen right now.

    :returns: the process name, or None when the screen cannot be read.
    """
    if sys.platform != "darwin":
        return None
    return _osascript(_FRONTMOST_SCRIPT)


def activate(app: str) -> bool:
    """Put ``app`` back in front, launching nothing.

    :param app: an application process name, as :func:`frontmost_app` reports it.
    :returns: whether macOS accepted the request.
    """
    if sys.platform != "darwin":
        return False
    quoted = app.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "System Events" to set frontmost of application process '
        f'"{quoted}" to true'
    )
    return _osascript(script) is not None


def remember(steal_focus: bool, headless: bool) -> str | None:
    """Note who owns the screen before the browser opens.

    :param steal_focus: whether this run may take the foreground at all.
    :param headless: whether this run opens a window at all.
    :returns: the frontmost application's name, or None when there is no screen to
        hand back — the run takes focus, runs headless, or cannot read the screen.
    """
    if steal_focus or headless:
        return None
    front = frontmost_app()
    if front is None:
        # The flag was asked for and macOS would not answer, most likely automation
        # permission: say so once, or the flag would quietly do nothing.
        print(
            "[focus] cannot read which app is frontmost, so --keep-background cannot act; "
            "allow automation of System Events if macOS asks",
            file=sys.stderr,
            flush=True,
        )
    return front


def _hand_back(previous: str, browser: str) -> None:
    """Give the screen back to ``previous`` whenever ``browser`` takes it, briefly.

    Runs on its own thread for a few seconds after the launch: Chromium activates
    itself a moment after its process starts, later than the launch call returns,
    so the hand-back watches for it instead of racing it.

    :param previous: the application that had the screen, from :func:`remember`.
    :param browser: the browser's application process name, from :func:`app_name`.
    """
    print(
        f"[focus] keeping {browser} behind your work; switch to it when you need it",
        file=sys.stderr,
        flush=True,
    )
    deadline = time.monotonic() + BUDGET_SECONDS
    handed = 0
    while time.monotonic() < deadline:
        front = frontmost_app()
        if front is None:
            # macOS said no (automation permission declined, no screen). Pushing blind
            # would fight the operator, so say once what the flag could not do and stop.
            print(
                f"[focus] cannot read which app is frontmost, so {browser} may still come "
                "forward; allow automation of System Events if macOS asks",
                file=sys.stderr,
                flush=True,
            )
            return
        if front == previous:
            return  # the screen never left, or the last hand-back worked
        if front == browser and handed < GIVE_BACKS:
            handed += 1
            activate(previous)
        time.sleep(POLL_SECONDS)


def keep_behind(previous: str | None, browser: str) -> threading.Thread | None:
    """Start the watcher that hands the screen back after the browser opens.

    :param previous: the application that had the screen, from :func:`remember`.
    :param browser: the browser's application process name, from :func:`app_name`.
    :returns: the started daemon thread, or None when there is nothing to watch for.
    """
    if previous is None:
        return None
    watcher = threading.Thread(
        target=_hand_back, args=(previous, browser), daemon=True, name="focus-watch"
    )
    watcher.start()
    return watcher
