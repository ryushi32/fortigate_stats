"""SNMP helpers using PySNMP 7.x asyncio API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
SNMP_V2C = 1

_snmp_engine: SnmpEngine | None = None
_hass_loop: asyncio.AbstractEventLoop | None = None


def configure_snmp_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the Home Assistant event loop for sync callers in worker threads."""
    global _hass_loop
    _hass_loop = loop


def _get_engine() -> SnmpEngine:
    global _snmp_engine
    if _snmp_engine is None:
        _snmp_engine = SnmpEngine()
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


def _table_prefix(oid: str) -> str:
    return oid if oid.endswith(".") else f"{oid}."


def _is_end_of_table(prefix: str, var_binds: tuple[ObjectType, ...]) -> bool:
    if not var_binds:
        return True
    oid = var_binds[0][0]
    oid_str = oid.prettyPrint() if hasattr(oid, "prettyPrint") else str(oid)
    return not str(oid_str).startswith(prefix)


async def async_snmp_getmulti(
    host: str, community: str, port: int, oids: tuple[str, ...]
) -> tuple[Any | None, tuple[Any, ...] | None]:
    """SNMP GET for one or more scalar OIDs."""
    engine = _get_engine()
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
    host: str, community: str, port: int, oid: str
) -> tuple[Any | None, list[tuple[Any, ...]]]:
    """Walk a single table column; returns rows as (oid, value) pairs."""
    engine = _get_engine()
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
        var_bind = ObjectType(ObjectIdentity(var_binds[0][0].prettyPrint()))

    return None, rows


async def async_snmp_getmultifromtable(
    host: str, community: str, port: int, oids: tuple[str, ...]
) -> tuple[Any | None, list[tuple[Any, ...]]]:
    """Walk a table using multiple column OIDs; each row is one tuple per column."""
    engine = _get_engine()
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
            ObjectType(ObjectIdentity(bind[0].prettyPrint())) for bind in response_binds
        )

    return None, rows


def _run_sync(coro) -> Any:
    loop = _hass_loop
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=DEFAULT_TIMEOUT + 10)
    return asyncio.run(coro)


def snmp_getmulti(
    host: str, community: str, port: int, oids: tuple[str, ...]
) -> tuple[Any | None, tuple[Any, ...] | None]:
    """Sync wrapper for worker threads (requires configure_snmp_loop)."""
    return _run_sync(async_snmp_getmulti(host, community, port, oids))


def snmp_getfromtable(
    host: str, community: str, port: int, oid: str
) -> tuple[Any | None, list[tuple[Any, ...]]]:
    """Sync wrapper for worker threads (requires configure_snmp_loop)."""
    return _run_sync(async_snmp_getfromtable(host, community, port, oid))


def snmp_getmultifromtable(
    host: str, community: str, port: int, oids: tuple[str, ...]
) -> tuple[Any | None, list[tuple[Any, ...]]]:
    """Sync wrapper for worker threads (requires configure_snmp_loop)."""
    return _run_sync(async_snmp_getmultifromtable(host, community, port, oids))
