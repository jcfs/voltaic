"""Shared device model.

Kept transport-agnostic: a Logitech mouse behind a Bolt receiver and a pair
of AirPods over Bluetooth end up as the same `Device` type, so the tray and
the panel do not need to know where a reading came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Charging states, normalised across every battery feature and protocol.
CHARGE_DISCHARGING = "discharging"
CHARGE_CHARGING = "charging"
CHARGE_SLOW = "charging slowly"
CHARGE_FULL = "fully charged"
CHARGE_ERROR = "charging error"
CHARGE_DISCONNECTED = "disconnected"


@dataclass
class Battery:
    percent: int | None = None
    status: str = CHARGE_DISCHARGING
    voltage_mv: int | None = None
    approximate: bool = False  # percent derived from coarse buckets
    rechargeable: bool = True
    source: str = ""  # which feature or protocol produced the reading

    @property
    def charging(self) -> bool:
        return self.status in (CHARGE_CHARGING, CHARGE_SLOW)

    @property
    def present(self) -> bool:
        return self.percent is not None and self.status != CHARGE_DISCONNECTED


@dataclass
class Cell:
    """One independently powered part of a device — an earbud, or the case."""

    label: str
    battery: Battery


@dataclass
class Device:
    index: int = 0
    name: str = ""
    kind: str = ""
    protocol: tuple[int, int] = (0, 0)
    battery: Battery | None = None
    path: str = ""
    transport: str = "hidpp"
    online: bool = True
    last_seen: float | None = None  # epoch seconds, set from the cache
    features: dict[int, int] = field(default_factory=dict)  # feature id -> index
    # Populated for devices made of several cells (AirPods). Single-battery
    # devices leave this empty and use `battery` instead.
    cells: list[Cell] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or f"Device {self.index}"

    @property
    def key(self) -> str:
        """Stable identity for caching across runs."""
        if self.transport == "hidpp":
            import os
            return f"{os.path.basename(self.path)}:{self.index}"
        return f"{self.transport}:{self.path}"

    @property
    def batteries(self) -> list[Battery]:
        """Every reading this device exposes, cells included."""
        if self.cells:
            return [cell.battery for cell in self.cells]
        return [self.battery] if self.battery else []

    @property
    def lowest_percent(self) -> int | None:
        """The reading that will run out first, ignoring absent parts.

        This is what drives the tray icon: for AirPods it deliberately
        ignores a case that is not currently reporting, so putting the buds
        in your ears does not make the icon jump to 0%.
        """
        levels = [b.percent for b in self.batteries
                  if b.present and b.percent is not None]
        return min(levels) if levels else None

    @property
    def any_charging(self) -> bool:
        return any(b.charging for b in self.batteries)

    @property
    def can_connect(self) -> bool:
        """May the host bring this device's link up on demand?

        Only true for Bluetooth accessories. A Logitech device behind a
        receiver associates itself when it is switched on — there is nothing
        the host can do to summon it, so offering a button would be a lie.
        """
        return self.transport == "airpods" and not self.online
