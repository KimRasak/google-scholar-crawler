"""Settings files: the same forty flags, written down once instead of retyped.

Crawling one topic over weeks means retyping the same rhythm, profile and filters every
session, and a mistyped delay costs a request. A TOML file holds those choices; the command
line still wins over the file, and the file still wins over the built-in defaults. Nothing is
guessed: an unknown key, a wrong type or a mode name in a settings file stops the run before a
single request goes out.
"""

from __future__ import annotations

import argparse
import difflib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

try:  # 3.11+ ships the reader; older versions need the backport
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - reported at load time
        tomllib = None  # type: ignore[assignment]

MODE_KEYS = frozenset(
    {
        "config",
        "doctor",
        "dry_run",
        "forget",
        "recipes",
        "rehearse_handoff",
        "self_check",
        "show_state",
    }
)
"""Keys a settings file may not set: they decide what the command does, not how it behaves."""


class ConfigError(ValueError):
    """A settings file cannot be used as written; the message names the key and the fix."""


class Origin(str, Enum):
    """Where a value in effect came from."""

    DEFAULT = "default"
    """Nobody chose it; the program's own default applies."""

    FILE = "settings file"
    """The settings file chose it and the command line did not override it."""

    COMMAND_LINE = "command line"
    """Passed as a flag, which beats the file."""


@dataclass(slots=True)
class Sources:
    """Where each setting in effect came from.

    :param path: the settings file that was read, when one was.
    :param origins: origin per argument name.
    :param overridden: keys the file set and the command line then overrode.
    """

    path: Path | None = None
    origins: dict[str, Origin] = field(default_factory=dict)
    overridden: tuple[str, ...] = ()

    def of(self, key: str) -> Origin:
        """Report where one setting came from.

        :param key: argument name, as spelled in the settings file.
        :returns: its origin; DEFAULT for anything nobody set.
        """
        return self.origins.get(key, Origin.DEFAULT)

    def from_file(self) -> list[str]:
        """List the settings the file is responsible for.

        :returns: argument names, sorted.
        """
        return sorted(key for key, origin in self.origins.items() if origin is Origin.FILE)

    def summary(self) -> str | None:
        """Report the file in one line, for a run that is not explaining itself.

        :returns: the line, or None when no settings file was read.
        """
        if self.path is None:
            return None
        beaten = f", {len(self.overridden)} overridden by flags" if self.overridden else ""
        return f"{len(self.from_file())} setting(s) from {self.path}{beaten}"

    def describe(self) -> list[str]:
        """Explain what the file contributed, if anything.

        :returns: printable lines; empty when no settings file was read.
        """
        if self.path is None:
            return []
        applied = self.from_file()
        lines = [f"settings file {self.path}: {len(applied)} value(s) in effect"]
        if applied:
            lines.append("  " + ", ".join(applied))
        for key in self.overridden:
            lines.append(f"  {key} came from the command line instead, which wins over the file")
        if not applied and not self.overridden:
            lines.append("  the file set nothing this run uses")
        return lines


def _normalize(key: str) -> str:
    """Accept a key spelled as the flag or as the argument name.

    :param key: key as written in the file.
    :returns: the argument name.
    """
    return key.strip().lstrip("-").replace("-", "_")


def _flatten(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """Merge one level of tables into flat argument names.

    Tables are for the reader's benefit only: ``[pacing]`` and top-level keys mean the same
    thing, so a file can be organised however its author likes.

    :param data: the parsed TOML document.
    :param path: file being read, for error messages.
    :returns: argument names mapped to their values.
    :raises ConfigError: when a table nests deeper than one level, or a key is set twice.
    """
    flat: dict[str, Any] = {}
    for key, value in data.items():
        entries = value.items() if isinstance(value, dict) else [(key, value)]
        for inner, setting in entries:
            if isinstance(setting, dict):
                raise ConfigError(f"{path}: [{key}.{inner}] nests too deep; settings are one level")
            name = _normalize(inner)
            if name in flat:
                raise ConfigError(f"{path}: {inner!r} is set twice")
            flat[name] = setting
    return flat


def _actions_by_dest(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    """Index a parser's options by argument name.

    :param parser: the parser whose options may be set from a file.
    :returns: actions keyed by dest, excluding help.
    """
    # argparse exposes no public view of its options; _actions is the only way to read them.
    return {action.dest: action for action in parser._actions if action.dest not in ("help",)}


def _unknown(name: str, known: Iterable[str], path: Path) -> ConfigError:
    """Build the error for a key the parser has no option for.

    :param name: the offending key.
    :param known: argument names a file may set.
    :param path: file being read.
    :returns: the error to raise.
    """
    close = difflib.get_close_matches(name, sorted(known), n=1)
    hint = f"; did you mean {close[0]!r}?" if close else ""
    return ConfigError(f"{path}: unknown setting {name!r}{hint}")


def _convert(value: Any, action: argparse.Action, name: str, path: Path) -> Any:
    """Turn one TOML value into what the option expects.

    :param value: the value as parsed from TOML.
    :param action: the option it sets.
    :param name: argument name, for error messages.
    :param path: file being read, for error messages.
    :returns: the converted value.
    :raises ConfigError: when the value is the wrong kind for this option.
    """
    wants_list = isinstance(action, argparse._AppendAction)
    if wants_list != isinstance(value, list):
        wanted = "a list of values" if wants_list else "a single value"
        raise ConfigError(f"{path}: {name!r} wants {wanted}")
    if wants_list:
        return [_convert_one(item, action, name, path) for item in value]
    return _convert_one(value, action, name, path)


def _convert_one(value: Any, action: argparse.Action, name: str, path: Path) -> Any:
    """Convert a single value for one option.

    :param value: the value as parsed from TOML.
    :param action: the option it sets.
    :param name: argument name, for error messages.
    :param path: file being read, for error messages.
    :returns: the converted value.
    :raises ConfigError: when the value is the wrong kind, or outside the option's choices.
    """
    flag = isinstance(action, argparse._StoreTrueAction | argparse._StoreFalseAction)
    if flag != isinstance(value, bool):
        wanted = "true or false" if flag else "a value, not true/false"
        raise ConfigError(f"{path}: {name!r} wants {wanted}")
    if flag:
        return value
    caster: Callable[[Any], Any] | None = action.type  # type: ignore[assignment]
    if caster in (int, float) and isinstance(value, str):
        raise ConfigError(f"{path}: {name!r} wants a number, not a string")
    try:
        converted = caster(value) if caster is not None else value
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{path}: {name!r} is not usable as written ({error})") from error
    if action.choices is not None and converted not in action.choices:
        allowed = ", ".join(str(choice) for choice in action.choices)
        raise ConfigError(f"{path}: {name!r} must be one of {allowed}")
    return converted


def read_settings(path: Path, parser: argparse.ArgumentParser) -> dict[str, Any]:
    """Read and validate a settings file against the options a parser accepts.

    :param path: TOML file to read.
    :param parser: the parser whose options the file may set.
    :returns: argument names mapped to converted values.
    :raises ConfigError: when the file is missing, unreadable, or sets something it may not.
    """
    if tomllib is None:  # pragma: no cover - only without the backport on 3.10
        raise ConfigError(
            "reading a settings file needs Python 3.11+ or the 'tomli' package "
            "(pip install tomli)"
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"{path}: no such settings file") from error
    except OSError as error:
        raise ConfigError(f"{path}: cannot be read ({error})") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: not valid TOML ({error})") from error
    actions = _actions_by_dest(parser)
    settable = {dest for dest in actions if dest not in MODE_KEYS}
    settings = {}
    for name, value in _flatten(raw, path).items():
        if name in MODE_KEYS:
            raise ConfigError(
                f"{path}: {name!r} decides what the command does, so it stays on the command line"
            )
        if name not in settable:
            raise _unknown(name, settable, path)
        settings[name] = _convert(value, actions[name], name, path)
    return settings


def given_on_command_line(parser: argparse.ArgumentParser, argv: list[str] | None) -> set[str]:
    """Report which options the command line actually named.

    Suppressing every default leaves only what was passed, which is what has to beat the file.

    :param parser: a throwaway parser instance; its defaults are suppressed in place.
    :param argv: argument vector, or None for ``sys.argv[1:]``.
    :returns: argument names given as flags.
    """
    for action in parser._actions:
        action.default = argparse.SUPPRESS
    return set(vars(parser.parse_args(argv)))


def apply_settings(
    args: argparse.Namespace,
    settings: dict[str, Any],
    explicit: set[str],
    path: Path,
) -> Sources:
    """Fill in the settings the command line left alone.

    :param args: parsed arguments, updated in place.
    :param settings: validated settings from the file.
    :param explicit: argument names the command line named.
    :param path: the settings file, for reporting.
    :returns: where every value in effect came from.
    """
    origins = {key: Origin.COMMAND_LINE for key in explicit if key not in MODE_KEYS}
    overridden = []
    for key, value in settings.items():
        if key in explicit:
            overridden.append(key)
            continue
        setattr(args, key, value)
        origins[key] = Origin.FILE
    return Sources(path=path, origins=origins, overridden=tuple(sorted(overridden)))


def resolve_settings(
    args: argparse.Namespace, parser: argparse.ArgumentParser, argv: list[str] | None
) -> Sources:
    """Apply ``--config`` to already-parsed arguments, command line winning.

    :param args: parsed arguments, updated in place.
    :param parser: a throwaway parser instance used to see what the command line named.
    :param argv: argument vector, or None for ``sys.argv[1:]``.
    :returns: where every value in effect came from.
    :raises ConfigError: when the file cannot be used as written.
    """
    if args.config is None:
        return Sources()
    settings = read_settings(args.config, parser)
    return apply_settings(args, settings, given_on_command_line(parser, argv), args.config)
