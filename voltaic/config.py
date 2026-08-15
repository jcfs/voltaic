"""User configuration.

Every setting used to live only in a command-line flag, which is fine when
you start Voltaic from a terminal and useless when you start it the normal
way — the desktop entry runs a bare `voltaic`, so nothing could be changed
at all without editing the launcher.

The file is JSON rather than TOML because `tomllib` only arrives in 3.11 and
this package supports 3.9, and writing TOML needs a dependency this project
does not otherwise have.

Precedence, lowest first: built-in defaults, the config file, command-line
flags. A flag therefore always wins for one run without editing anything.
"""

from __future__ import annotations

import json
import os
import tempfile

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "voltaic", "config.json",
)

# Sources are the device families Voltaic knows how to read. The two that
# need no configuration are on by default; the generic ones are off, because
# they can surface devices the user did not ask this program to care about
# (a laptop battery, a phone) and duplicating what the desktop already shows
# is worse than showing nothing.
DEFAULTS: dict = {
    "interval": 900.0,
    "notify": True,
    "low_percent": 20,
    "tray": "auto",
    "sources": {
        "hidpp": True,
        "airpods": True,
        "upower": False,
        "bluez": False,
    },
    # Per-device overrides, keyed by the device key shown in `--list --keys`:
    #   {"hidraw3:1": {"name": "Desk keyboard", "hidden": false}}
    "devices": {},
}


def _merge(defaults: dict, loaded: dict) -> dict:
    """Overlay `loaded` on `defaults`, one level deep for dict values.

    A partial config file must keep working when new settings are added, so
    missing keys fall back rather than disappearing.
    """
    result = dict(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            merged = dict(result[key])
            merged.update(value)
            result[key] = merged
        else:
            result[key] = value
    return result


def load(path: str | None = None) -> dict:
    """The effective configuration.

    A broken or unreadable file falls back to the defaults rather than
    stopping the program: a typo in a config file should not cost you your
    battery indicator.
    """
    path = path or CONFIG_PATH
    try:
        with open(path) as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(loaded, dict):
        return dict(DEFAULTS)
    return _merge(DEFAULTS, loaded)


def save(config: dict, path: str | None = None) -> None:
    """Write the configuration, replacing it atomically."""
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".json")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(config, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def enabled_sources(config: dict) -> list[str]:
    """Names of the sources switched on, in a stable order."""
    configured = config.get("sources", {})
    return [name for name in DEFAULTS["sources"]
            if configured.get(name, DEFAULTS["sources"][name])]


def device_override(config: dict, key: str) -> dict:
    """Per-device settings for one device key."""
    devices = config.get("devices", {})
    entry = devices.get(key)
    return entry if isinstance(entry, dict) else {}


def apply_overrides(config: dict, devices: list) -> list:
    """Rename and hide devices according to the configuration.

    Hiding is applied here rather than at the source, so a hidden device
    still costs nothing extra to skip and can be un-hidden without a
    restart.
    """
    result = []
    for device in devices:
        override = device_override(config, device.key)
        if override.get("hidden"):
            continue
        name = override.get("name")
        if name:
            device.name = name
        result.append(device)
    return result


def set_device(config: dict, key: str, **settings) -> dict:
    """Return a copy of `config` with one device's overrides updated.

    A setting of None removes that override, so "forget the custom name"
    does not need a separate call.
    """
    devices = dict(config.get("devices", {}))
    entry = dict(devices.get(key, {}))
    for name, value in settings.items():
        if value is None:
            entry.pop(name, None)
        else:
            entry[name] = value
    if entry:
        devices[key] = entry
    else:
        devices.pop(key, None)
    updated = dict(config)
    updated["devices"] = devices
    return updated
