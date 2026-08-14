"""Remembers what each device looked like when it was last reachable.

A device that is powered off or switched to another host still answers the
receiver's pairing slot, but nothing else — no name, no battery. Without a
little persistence it would show up as an anonymous "Device 1" with no
charge, which looks like a bug. This module fills those gaps back in.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from .model import Battery, Cell, Device

CACHE_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "voltaic", "devices.json",
)

# Drop entries for devices we have not seen in a long while, so unpairing a
# device eventually stops it haunting the panel.
MAX_AGE_SECONDS = 30 * 24 * 3600


def load() -> dict:
    try:
        with open(CACHE_PATH) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    now = time.time()
    return {key: entry for key, entry in data.items()
            if isinstance(entry, dict)
            and now - entry.get("seen", 0) < MAX_AGE_SECONDS}


def save(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CACHE_PATH), suffix=".json")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(cache, handle, indent=1)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def reconcile(devices: list[Device]) -> list[Device]:
    """Record what is online and restore what is not.

    Returns the same list, with offline devices enriched from the cache.
    """
    cache = load()
    changed = False

    for device in devices:
        if device.online:
            entry = {
                "name": device.name,
                "kind": device.kind,
                "seen": time.time(),
            }
            if device.battery and device.battery.percent is not None:
                entry["percent"] = device.battery.percent
                entry["status"] = device.battery.status
            if device.cells:
                entry["cells"] = [
                    {"label": cell.label,
                     "percent": cell.battery.percent,
                     "status": cell.battery.status}
                    for cell in device.cells
                ]
            cache[device.key] = entry
            changed = True
        else:
            entry = cache.get(device.key)
            if not entry:
                continue
            device.name = device.name or entry.get("name", "")
            device.kind = device.kind or entry.get("kind", "")
            device.last_seen = entry.get("seen")
            if entry.get("percent") is not None:
                device.battery = Battery(percent=entry["percent"],
                                         status=entry.get("status", ""),
                                         source="cached")
            if entry.get("cells") and not device.cells:
                device.cells = [
                    Cell(label=item.get("label", "?"),
                         battery=Battery(percent=item.get("percent"),
                                         status=item.get("status", ""),
                                         source="cached"))
                    for item in entry["cells"]
                ]

    if changed:
        save(cache)
    return devices


def describe_age(seen: float | None) -> str:
    """Human-friendly 'last seen' text."""
    if not seen:
        return "offline"
    age = max(0, time.time() - seen)
    if age < 90:
        return "offline · seen just now"
    if age < 3600:
        return f"offline · seen {int(age // 60)}m ago"
    if age < 86400:
        return f"offline · seen {int(age // 3600)}h ago"
    return f"offline · seen {int(age // 86400)}d ago"
