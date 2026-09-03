"""Checking that this machine can run a crawl at all, before one is attempted.

Everything a first run needs — the right Python, the three libraries, a browser Playwright can
drive, a writable profile and output directory — fails in a different place with a different
library's error message. This module checks all of it locally, in one pass, and names the exact
command that fixes each problem. Nothing here contacts Scholar; ``--self-check`` does that
afterwards, once the environment is known to be sound.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from itertools import takewhile
from pathlib import Path

from .config import tomllib
from .machine import version

MINIMUM_PYTHON = (3, 10)
"""The floor declared in pyproject.toml."""

@dataclass(slots=True, frozen=True)
class Requirement:
    """One library the crawler needs.

    :param module: import name.
    :param distribution: name it is installed under, which differs for beautifulsoup4.
    :param minimum: lowest version the code is written against, as in pyproject.toml.
    """

    module: str
    distribution: str
    minimum: tuple[int, ...]


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement("playwright", "playwright", (1, 49)),
    Requirement("bs4", "beautifulsoup4", (4, 12)),
    Requirement("lxml", "lxml", (5, 0)),
)
"""The three dependencies declared in pyproject.toml, with the floors declared there."""

INSTALL_COMMAND = "pip install -e ."
"""What fixes a missing or too-old dependency, from a checkout."""

BROWSER_COMMAND = "scholar-crawler --install-browser"
"""What downloads the browser Playwright drives, in whichever environment holds this tool."""

CHROME_PATHS: dict[str, tuple[str, ...]] = {
    "chrome": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/opt/google/chrome/chrome",
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    ),
    "msedge": (
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/opt/microsoft/msedge/msedge",
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ),
}
"""Where each channel installs itself, per platform."""

CHROME_COMMANDS: dict[str, tuple[str, ...]] = {
    "chrome": ("google-chrome", "google-chrome-stable", "chrome"),
    "msedge": ("microsoft-edge", "msedge"),
}
"""Names the same browsers use on PATH."""


class Status(str, Enum):
    """How a single check came out."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


MARKS: dict[Status, str] = {Status.OK: "+", Status.WARN: "!", Status.FAIL: "x"}
"""ASCII marks, so the report is readable in a terminal without symbol fonts."""


@dataclass(slots=True, frozen=True)
class Finding:
    """One environment check.

    :param name: what was checked.
    :param status: how it came out.
    :param detail: what was found.
    :param fix: the command or action that resolves it, when it needs resolving.
    """

    name: str
    status: Status
    detail: str
    fix: str = ""

    def describe(self) -> str:
        """Format the finding as one line.

        :returns: mark, name and detail.
        """
        return f"{MARKS[self.status]} {self.name:22} {self.detail}"


def check_python() -> Finding:
    """Check the running interpreter against the declared minimum.

    :returns: the finding.
    """
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info[:2] >= MINIMUM_PYTHON:
        return Finding("python", Status.OK, f"{version} at {sys.executable}")
    floor = ".".join(str(part) for part in MINIMUM_PYTHON)
    return Finding(
        "python",
        Status.FAIL,
        f"{version} is older than the required {floor}",
        f"install Python {floor} or newer and rerun in that interpreter",
    )


def check_toml_reader() -> Finding:
    """Check that a settings file could be read on this interpreter.

    ``tomllib`` is stdlib from 3.11; older interpreters need the ``tomli`` backport, and a
    ``--config`` run is the wrong moment to find that out.

    :returns: the finding.
    """
    if sys.version_info[:2] >= (3, 11):
        return Finding("settings files", Status.OK, "tomllib (stdlib) reads --config files")
    try:
        import tomli  # noqa: F401 - presence is the whole check
    except ModuleNotFoundError:
        return Finding(
            "settings files",
            Status.WARN,
            "--config needs the tomli backport on this Python",
            "pip install tomli, or run on Python 3.11+",
        )
    return Finding("settings files", Status.OK, "tomli reads --config files")


def _version_of(requirement: Requirement) -> str:
    """Read the installed version of a requirement.

    :param requirement: the library to look up.
    :returns: the version string, or an empty string when nothing reports one.
    """
    try:
        return metadata.version(requirement.distribution)
    except metadata.PackageNotFoundError:  # imported from a source tree, not installed
        return str(getattr(importlib.import_module(requirement.module), "__version__", ""))


def _as_numbers(version: str) -> tuple[int, ...]:
    """Turn a version string into comparable numbers.

    :param version: version as reported by the package.
    :returns: the leading numeric components, empty when there are none.
    """
    numbers = []
    for part in version.split("."):
        digits = "".join(takewhile(str.isdigit, part))
        if not digits:
            break
        numbers.append(int(digits))
    return tuple(numbers)


def check_module(requirement: Requirement) -> Finding:
    """Check that a required library imports and is new enough.

    :param requirement: the library to check.
    :returns: the finding.
    """
    try:
        importlib.import_module(requirement.module)
    except ImportError as error:
        return Finding(requirement.module, Status.FAIL, f"cannot be imported: {error}", INSTALL_COMMAND)
    version = _version_of(requirement)
    floor = ".".join(str(part) for part in requirement.minimum)
    numbers = _as_numbers(version)
    if numbers and numbers < requirement.minimum:
        return Finding(
            requirement.module,
            Status.FAIL,
            f"{version} is older than the required {floor}",
            f"{INSTALL_COMMAND} --upgrade",
        )
    return Finding(requirement.module, Status.OK, version or f"installed, version unknown (needs {floor}+)")


PROBE = (
    "from playwright.sync_api import sync_playwright\n"
    "with sync_playwright() as playwright:\n"
    "    print(playwright.chromium.executable_path)\n"
)
"""Script that asks Playwright where its Chromium is; see :func:`_bundled_chromium`."""


def _bundled_chromium() -> tuple[bool, str, str]:
    """Ask Playwright whether its own Chromium has been downloaded.

    The question is answered in a child process because reading the path starts Playwright's
    driver, and a driver session that never launches a browser prints asyncio teardown noise on
    some versions (1.62 does). Keeping only the child's stdout means the first command a new
    user runs reports one clean line either way.

    :returns: whether the download is present, a phrase describing where it is or what is
        missing, and the command that would fix it.
    """
    probe = subprocess.run(  # noqa: S603 - fixed script, no user input
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    reported = probe.stdout.strip().splitlines()
    if probe.returncode != 0 or not reported:
        if "ModuleNotFoundError" in probe.stderr:
            return False, "playwright is not installed", INSTALL_COMMAND
        detail = next(
            (line for line in reversed(probe.stderr.strip().splitlines()) if line.strip()),
            "no output",
        )
        return False, f"playwright cannot report a browser: {detail}", BROWSER_COMMAND
    executable = Path(reported[-1])
    if executable.exists():
        return True, f"bundled Chromium at {executable}", ""
    return False, f"bundled Chromium not downloaded (expected at {executable})", BROWSER_COMMAND


def _channel_path(channel: str) -> str | None:
    """Locate an installed browser channel.

    :param channel: channel name such as ``chrome`` or ``msedge``.
    :returns: the path a run would launch, or None when the channel is not installed.
    """
    for candidate in CHROME_PATHS.get(channel, ()):
        if Path(candidate).exists():
            return candidate
    for command in CHROME_COMMANDS.get(channel, ()):
        if found := shutil.which(command):
            return found
    return None


def check_browser(channel: str | None) -> Finding:
    """Check the browser this run would drive, which is the only one that has to exist.

    A run launches the requested channel, so a missing bundled Chromium costs nothing while
    Chrome is installed — and downloading it would not fix a missing channel either. Reporting
    the two as separate requirements told a caller to spend 150 MB on a working setup, and made
    ``--doctor`` exit 1 on a machine where a crawl runs.

    :param channel: the channel a run would use, or None for the bundled Chromium.
    :returns: the finding, naming the browser that will be launched.
    """
    downloaded, bundled, fix = _bundled_chromium()
    if not channel:
        if downloaded:
            return Finding("browser", Status.OK, f"{bundled} (no channel requested)")
        return Finding("browser", Status.FAIL, bundled, fix)
    if found := _channel_path(channel):
        spare = "bundled Chromium is also available" if downloaded else "no bundled Chromium as a spare"
        return Finding("browser", Status.OK, f"{channel} at {found}; {spare}")
    if downloaded:
        return Finding(
            "browser",
            Status.FAIL,
            f"{channel} was not found in the usual places, though {bundled} is ready",
            f"install {channel}, or run with --channel '' to drive the bundled Chromium",
        )
    return Finding(
        "browser",
        Status.FAIL,
        f"{channel} was not found in the usual places, and {bundled}",
        f"install {channel}, or {fix} and run with --channel ''",
    )


def _absolute(path: Path) -> Path:
    """Make a path absolute without resolving symlinks.

    :param path: the path as the user wrote it.
    :returns: the absolute form.
    """
    return path if path.is_absolute() else Path.cwd() / path


def _nearest_existing(directory: Path) -> Path:
    """Find the closest ancestor that exists, so writability can be tested without creating it.

    :param directory: the directory a run would use.
    :returns: ``directory`` itself when it exists, otherwise its closest existing ancestor.
    """
    target = _absolute(directory)
    for candidate in (target, *target.parents):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:  # an ancestor this user may not even look at; keep walking up
            continue
    return Path(target.anchor or ".")


def check_writable(name: str, path: Path, *, kind: str) -> Finding:
    """Check that a directory can be written to, or created when it does not exist yet.

    A diagnostic must not leave directories behind for a mistyped path, so the check probes the
    closest ancestor that already exists instead of creating the target.

    :param name: label for the report.
    :param path: the directory itself, or the file whose directory is checked.
    :param kind: ``dir`` when ``path`` is the directory, ``file`` when it is a file in it.
    :returns: the finding.
    """
    directory = path if kind == "dir" else path.parent
    existing = _nearest_existing(directory)
    probe = existing / ".scholar-write-test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        reason = error.strerror or error
        probed = "" if existing == _absolute(directory) else f" (nearest existing: {existing})"
        return Finding(
            name,
            Status.FAIL,
            f"{directory} cannot be written to: {reason}{probed}",
            "point the flag at a writable directory, or fix the permissions",
        )
    if existing != _absolute(directory):
        return Finding(name, Status.OK, f"{directory} does not exist yet; {existing} is writable")
    return Finding(name, Status.OK, f"{directory} is writable")


def check_profile(profile: Path) -> Finding:
    """Report whether the persistent profile already holds cleared cookies.

    :param profile: the profile directory a run would reuse.
    :returns: the finding.
    """
    writable = check_writable("profile", profile, kind="dir")
    if writable.status is Status.FAIL:
        return writable
    cookies = profile / "Default" / "Cookies"
    if cookies.exists():
        return Finding("profile", Status.OK, f"{profile} carries cookies from earlier runs")
    return Finding(
        "profile",
        Status.WARN,
        f"{profile} holds no cookies yet, so the first challenge will need a human",
        "expect one takeover on the first run; the cleared cookies are then reused",
    )


def check_version() -> Finding:
    """Compare the version pip recorded with the version of the code being run.

    An editable install keeps the metadata written when it was installed, so after a ``git
    pull`` the tool can report a version it is not running — which then reaches every ``--json``
    document and every bug report.

    :returns: the finding, naming both versions when they disagree.
    """
    installed = version()
    source = _source_version()
    if source is None or installed == source:
        return Finding("version", Status.OK, installed)
    return Finding(
        "version",
        Status.WARN,
        f"pip recorded {installed} but this checkout is {source}",
        f"{INSTALL_COMMAND} again so --version and --json report {source}",
    )


def _source_version() -> str | None:
    """Read the version from the ``pyproject.toml`` next to the package, when there is one.

    :returns: the declared version, or None when the package was installed without its sources
        or the file cannot be parsed.
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if tomllib is None or not pyproject.exists():
        return None
    try:
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None
    project = declared.get("project")
    return project.get("version") if isinstance(project, dict) else None


def diagnose_environment(
    *, profile: Path, out: Path, state: Path, channel: str | None
) -> list[Finding]:
    """Run every environment check.

    :param profile: profile directory a run would reuse.
    :param out: JSONL destination a run would write.
    :param state: resume-state file a run would write.
    :param channel: browser channel a run would drive.
    :returns: the findings, in the order they are worth reading.
    """
    findings = [check_python(), check_version()]
    findings.extend(check_module(requirement) for requirement in REQUIREMENTS)
    findings.append(check_toml_reader())
    findings.append(check_browser(channel))
    findings.append(check_profile(profile))
    findings.append(check_writable("output", out, kind="file"))
    if state.parent != out.parent:
        findings.append(check_writable("state", state, kind="file"))
    return findings


def render_environment(findings: list[Finding]) -> list[str]:
    """Format the environment report, fixes last.

    :param findings: what the checks found.
    :returns: printable lines.
    """
    failures = [finding for finding in findings if finding.status is Status.FAIL]
    warnings = [finding for finding in findings if finding.status is Status.WARN]
    lines = [finding.describe() for finding in findings]
    if failures:
        counted = "1 problem" if len(failures) == 1 else f"{len(failures)} problems"
        lines.append(f"{counted} must be fixed before a crawl can run:")
    elif warnings:
        lines.append("nothing is broken; these are worth knowing:")
    else:
        lines.append("this machine is ready; run --self-check next to test Scholar itself")
    for finding in (*failures, *warnings):
        if finding.fix:
            lines.append(f"  {finding.name}: {finding.fix}")
    return lines
