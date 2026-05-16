"""SNMP helpers using PySNMP 7.x asyncio API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from pysnmp.error import PySnmpError
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    Udp6TransportTarget,
    UdpTransportTarget,
    get_cmd,
    next_cmd,
)
from pysnmp.hlapi.v3arch.asyncio.cmdgen import LCD
from pysnmp.smi import view

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
SNMP_V2C = 1

_snmp_engine: SnmpEngine | None = None
_engine_lock = asyncio.Lock()


def _build_snmp_engine() -> SnmpEngine:
    """Create SnmpEngine and preload MIBs (blocking — run in executor)."""
    engine = SnmpEngine()
    mib_view_controller = view.MibViewController(
        engine.message_dispatcher.mib_instrum_controller.get_mib_builder()
    )
    engine.cache["mibViewController"] = mib_view_controller
    mib_view_controller.mibBuilder.load_modules()
    return engine


async def async_setup_snmp(hass: HomeAssistant) -> SnmpEngine:
    """Initialize the shared SNMP engine off the event loop."""
    global _snmp_engine

    if _snmp_engine is not None:
        return _snmp_engine

    async with _engine_lock:
        if _snmp_engine is None:
            engine = await hass.async_add_executor_job(_build_snmp_engine)
            _snmp_engine = engine

            @callback
            def _async_shutdown(_event) -> None:
                LOGGER.debug("Unconfiguring SNMP engine")
                LCD.unconfigure(engine, None)

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_shutdown)

    return _snmp_engine


async def _get_engine(hass: HomeAssistant) -> SnmpEngine:
    if _snmp_engine is None:
        return await async_setup_snmp(hass)
    return _snmp_engine


async def _create_target(host: str, port: int) -> UdpTransportTarget | Udp6TransportTarget:
    try:
        return await UdpTransportTarget.create(
            (host, port), timeout=DEFAULT_TIMEOUT
        )
    except PySnmpError:
        return await Udp6TransportTarget.create(
            (host, port), timeout=DEFAULT_TIMEOUT
        )


def _community(community: str) -> CommunityData:
    return CommunityData(community, mpModel=SNMP_V2C)


def _row_from_binds(var_binds: tuple[ObjectType, ...]) -> tuple[Any, ...]:
    """Return (oid, value) pairs compatible with legacy prettyPrint() callers."""
    return tuple((bind[0], bind[1]) for bind in var_binds)


def _oid_str(oid_obj: Any) -> str:
    """Return OID string from an SNMP object identity."""
    if hasattr(oid_obj, "prettyPrint"):
        try:
            return oid_obj.prettyPrint()
        except Exception:
            pass
    return str(oid_obj)


def _table_prefix(oid: str) -> str:
    return oid if oid.endswith(".") else f"{oid}."


def _is_end_of_table(prefix: str, var_binds: tuple[ObjectType, ...]) -> bool:
    if not var_binds:
        return True
    return not _oid_str(var_binds[0][0]).startswith(prefix)


async def async_snmp_getmulti(
    hass: HomeAssistant,
    host: str,
    community: str,
    port: int,
    oids: tuple[str, ...],
) -> tuple[Any | None, tuple[Any, ...] | None]:
    """SNMP GET for one or more scalar OIDs."""
    engine = await _get_engine(hass)
    target = await _create_target(host, port)
    auth = _community(community)
    context = ContextData()

    error_indication, error_status, _error_index, var_binds = await get_cmd(
        engine,
        auth,
        target,
        context,
        *[ObjectType(ObjectIdentity(oid)) for oid in oids],
        lookupMib=False,
    )

    if error_indication:
        return error_indication, None
    if error_status:
        return error_status.prettyPrint(), None

    return None, _row_from_binds(var_binds)


async def async_snmp_getfromtable(
    hass: HomeAssistant,
    host: str,
    community: str,
    port: int,
    oid: str,
) -> tuple[Any | None, list[tuple[Any, ...]]]:
    """Walk a single table column; returns rows as (oid, value) pairs."""
    engine = await _get_engine(hass)
    target = await _create_target(host, port)
    auth = _community(community)
    context = ContextData()
    prefix = _table_prefix(oid)

    rows: list[tuple[Any, ...]] = []
    var_bind = ObjectType(ObjectIdentity(oid))

    while True:
        error_indication, error_status, _error_index, var_binds = await next_cmd(
            engine,
            auth,
            target,
            context,
            var_bind,
            lookupMib=False,
            lexicographicMode=False,
        )

        if error_indication:
            return error_indication, rows
        if error_status:
            return error_status.prettyPrint(), rows
        if not var_binds or _is_end_of_table(prefix, var_binds):
            break

        rows.append(_row_from_binds(var_binds))
        var_bind = ObjectType(ObjectIdentity(_oid_str(var_binds[0][0])))

    return None, rows


async def async_snmp_getmultifromtable(
    hass: HomeAssistant,
    host: str,
    community: str,
    port: int,
    oids: tuple[str, ...],
) -> tuple[Any | None, list[tuple[Any, ...]]]:
    """Walk a table using multiple column OIDs; each row is one tuple per column."""
    engine = await _get_engine(hass)
    target = await _create_target(host, port)
    auth = _community(community)
    context = ContextData()
    prefix = _table_prefix(oids[0])

    rows: list[tuple[Any, ...]] = []
    var_binds = tuple(ObjectType(ObjectIdentity(oid)) for oid in oids)

    while True:
        error_indication, error_status, _error_index, response_binds = await next_cmd(
            engine,
            auth,
            target,
            context,
            *var_binds,
            lookupMib=False,
            lexicographicMode=False,
        )

        if error_indication:
            return error_indication, rows
        if error_status:
            return error_status.prettyPrint(), rows
        if not response_binds or _is_end_of_table(prefix, response_binds):
            break

        rows.append(_row_from_binds(response_binds))
        var_binds = tuple(
            ObjectType(ObjectIdentity(_oid_str(bind[0]))) for bind in response_binds
        )

    return None, rows
