"""Persistent storage for monthly interface bandwidth totals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


class MonthlyBandwidthStorage:
    """Track per-interface upload/download bytes for the current calendar month."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.monthly_bandwidth.{entry_id}",
        )
        self._month = _current_month()
        self._interfaces: dict[str, dict[str, int]] = {}

    @property
    def month(self) -> str:
        return self._month

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        self._month = data.get("month", _current_month())
        self._interfaces = data.get("interfaces", {})

    async def async_save(self) -> None:
        await self._store.async_save(
            {"month": self._month, "interfaces": self._interfaces}
        )

    def _ensure_month(self) -> None:
        month = _current_month()
        if month != self._month:
            self._month = month
            self._interfaces = {}

    def _ensure_interface(self, if_id: str) -> dict[str, int]:
        if if_id not in self._interfaces:
            self._interfaces[if_id] = {"rx_total": 0, "tx_total": 0}
        return self._interfaces[if_id]

    def add_delta(self, if_id: str, rx_bytes: int, tx_bytes: int) -> None:
        """Add received (down) and transmitted (up) byte deltas for an interface."""
        self._ensure_month()
        iface = self._ensure_interface(if_id)
        if rx_bytes > 0:
            iface["rx_total"] += rx_bytes
        if tx_bytes > 0:
            iface["tx_total"] += tx_bytes

    def get_totals(self, if_id: str) -> tuple[int, int]:
        """Return (download_bytes, upload_bytes) for the current month."""
        self._ensure_month()
        iface = self._interfaces.get(if_id, {})
        return iface.get("rx_total", 0), iface.get("tx_total", 0)
