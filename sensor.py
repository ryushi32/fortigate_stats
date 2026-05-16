"""Sensor platform for FortiGate Stats."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval

# pylint: disable=unused-wildcard-import
from .const import *  # noqa: F403
from .snmp import (
    async_setup_snmp,
    async_snmp_getfromtable,
    async_snmp_getmulti,
    async_snmp_getmultifromtable,
)

# pylint: enable=unused-wildcard-import

from homeassistant.const import (  # noqa: E402
    CONF_IP_ADDRESS,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities
) -> None:
    """Set up FortiGate Stats sensors from a config entry."""
    await async_setup_snmp(hass)

    monitor = SnmpStatisticsMonitor(hass, config_entry, async_add_entities)
    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = {"monitor": monitor}

    await monitor.async_update()

    interval = timedelta(
        seconds=monitor.update_interval_seconds or DEFAULT_SCAN_INTERVAL
    )

    @callback
    def _async_poll(now) -> None:
        hass.async_create_task(monitor.async_update())

    config_entry.async_on_unload(
        async_track_time_interval(hass, _async_poll, interval)
    )


class SnmpStatisticsSensor(Entity):
    """FortiGate SNMP sensor."""

    def __init__(self, entity_id, fw_info, name=None, unit=None, icon=None):
        self._attributes = {}
        self._state = "unknown"
        self.fw_info = fw_info
        self.entity_id = entity_id
        self._name = name if name is not None else entity_id
        self._unitofmeasurement = unit
        self._icon = icon if icon is not None else "mdi:eye"
        _LOGGER.info("Created sensor %s", entity_id)

    def set_state(self, state):
        """Set the state."""
        if self._state == state:
            return
        self._state = state
        self.async_write_ha_state()

    def set_attributes(self, attributes):
        """Set the state attributes."""
        self._attributes = attributes
        self.async_write_ha_state()

    @property
    def icon(self):
        return self._icon

    @property
    def unique_id(self) -> str:
        return self.entity_id

    @property
    def should_poll(self):
        return False

    @property
    def extra_state_attributes(self):
        return self._attributes

    @property
    def unit_of_measurement(self):
        return self._unitofmeasurement

    @property
    def state(self):
        return self._state

    @property
    def name(self):
        return self._name

    @property
    def device_info(self):
        identifier = {(DOMAIN, self.fw_info[OID_SERIALNUMBER])}
        return {
            "identifiers": identifier,
            "name": self.fw_info[OID_HOSTNAME],
            "manufacturer": "Fortinet",
            "model": self.fw_info[OID_MODEL],
            "sw_version": self.fw_info[OID_FORTIOS],
        }


class SnmpStatisticsMonitor:
    """Poll FortiGate SNMP metrics and manage sensor entities."""

    def __init__(self, hass, config_entry, async_add_entities):
        self.hass = hass
        self.config_entry = config_entry
        self.async_add_entities = async_add_entities
        self.meter_sensors = {}
        self.current_if_data = {}
        self.current_if_data_time = 0

        self.username = config_entry.data.get(CONF_USERNAME)
        self.target_ip = config_entry.data.get(CONF_IP_ADDRESS)
        self.port = config_entry.data.get(CONF_PORT, DEFAULT_PORT)
        self.update_interval_seconds = config_entry.data.get(CONF_SCAN_INTERVAL)

        self.include_cpu_and_ram = config_entry.data.get(CONF_CPUANDRAM)
        self.include_disk = config_entry.data.get(CONF_DISK)
        self.include_sessions = config_entry.data.get(CONF_SESSIONS)

        self.include_interfaces = config_entry.data.get(CONF_INTERFACESYESNO)
        self.interfaces = set()
        if self.include_interfaces:
            selected = config_entry.data.get(CONF_INTERFACES) or []
            if isinstance(selected, dict):
                selected = list(selected)
            self.interfaces = set(selected)
            self.interfacesbandwidth = config_entry.data.get(CONF_INTERFACESBANDWIDTH)
            self.interfacesoctets = config_entry.data.get(CONF_INTERFACESOCTETS)

        self.include_performanceslas = config_entry.data.get(CONF_PERFORMANCESLASYESNO)
        self.performance_slas = set()
        if self.include_performanceslas:
            selected = config_entry.data.get(CONF_PERFORMANCESLAS) or []
            if isinstance(selected, dict):
                selected = list(selected)
            self.performance_slas = set(selected)
            self.include_performanceslasstate = config_entry.data.get(
                CONF_PERFORMANCESLASSTATE
            )
            self.include_performanceslaslinkmetrics = config_entry.data.get(
                CONF_PERFORMANCESLASLINKMETRICS
            )
            self.include_performanceslasbandwidthprobe = config_entry.data.get(
                CONF_PERFORMANCESLASBANDWIDTHPROBE
            )

        self.fw_info = {
            OID_HOSTNAME: config_entry.data.get(OID_HOSTNAME),
            OID_SERIALNUMBER: config_entry.data.get(OID_SERIALNUMBER),
            OID_MODEL: config_entry.data.get(OID_MODEL),
            OID_FORTIOS: config_entry.data.get(OID_FORTIOS),
        }

    async def async_update(self) -> None:
        """Refresh SNMP data and update sensor entities."""
        try:
            if self.include_interfaces and self.interfaces:
                await self._async_update_netif_stats()
            await self._async_add_or_update_entities()
        except Exception:
            _LOGGER.exception("Error updating FortiGate Stats sensors")

    async def _async_update_netif_stats(self) -> None:
        if_data = self.current_if_data
        oids = (OID_IFNAME, OID_IFALIAS, OID_IFHCINOCTETS, OID_IFHCOUTOCTETS)
        error_indication, snmp_data = await async_snmp_getmultifromtable(
            self.hass, self.target_ip, self.username, self.port, oids
        )
        if error_indication:
            _LOGGER.warning("Interface SNMP error: %s", error_indication)
            return

        for interface in if_data:
            if_data[interface]["rx_octets_prev"] = if_data[interface]["rx_octets"]
            if_data[interface]["tx_octets_prev"] = if_data[interface]["tx_octets"]

        for if_name, if_alias, if_hcinoctets, if_hcoutoctets in snmp_data:
            if_id = if_name[0].prettyPrint()
            if if_id not in self.interfaces:
                continue

            if if_id not in if_data:
                if_data[if_id] = {
                    "name": "",
                    "alias": "",
                    "rx_octets": -1,
                    "tx_octets": -1,
                    "rx_speed_octets": -1.0,
                    "tx_speed_octets": -1.0,
                    "rx_octets_prev": -1.0,
                    "tx_octets_prev": -1.0,
                    "last_stat_time": time.time(),
                    "rx_diff": -1,
                    "tx_diff": -1,
                }

            if_data[if_id]["name"] = if_name[1].prettyPrint()
            if_data[if_id]["alias"] = if_alias[1].prettyPrint()
            if_data[if_id]["rx_octets"] = int(if_hcinoctets[1].prettyPrint())
            if_data[if_id]["tx_octets"] = int(if_hcoutoctets[1].prettyPrint())

        new_if_data_time = time.time()
        for if_id in list(self.current_if_data):
            cur_data = self.current_if_data[if_id]
            timediff_stat_seconds = new_if_data_time - cur_data["last_stat_time"]

            rx_diff = cur_data["rx_octets"] - cur_data["rx_octets_prev"]
            tx_diff = cur_data["tx_octets"] - cur_data["tx_octets_prev"]

            cur_data["rx_diff"] = rx_diff
            cur_data["tx_diff"] = tx_diff

            if timediff_stat_seconds < 1:
                continue

            if rx_diff == 0 and tx_diff == 0 and timediff_stat_seconds < 4:
                continue

            cur_data["rx_speed_octets"] = rx_diff / timediff_stat_seconds
            cur_data["tx_speed_octets"] = tx_diff / timediff_stat_seconds
            cur_data["last_stat_time"] = new_if_data_time

        self.current_if_data = if_data
        self.current_if_data_time = new_if_data_time

    def _add_or_update_entity(
        self, entity_id, friendlyname, value, unit, icon, attributes=None
    ):
        if entity_id in self.meter_sensors:
            sensor = self.meter_sensors[entity_id]
            sensor.set_state(value)
        else:
            sensor = SnmpStatisticsSensor(
                entity_id, self.fw_info, friendlyname, unit, icon
            )
            sensor._state = value
            self.async_add_entities([sensor])
            self.meter_sensors[entity_id] = sensor

        if attributes is not None:
            self.meter_sensors[entity_id].set_attributes(attributes)

    async def _async_add_or_update_entities(self) -> None:
        serial = self.fw_info[OID_SERIALNUMBER].replace(".", "_")
        prefix = f"sensor.{DOMAIN}_{serial}_"

        for if_id in self.current_if_data:
            cur_if_data = self.current_if_data[if_id]
            if_name = cur_if_data["name"]
            if_alias = cur_if_data["alias"]
            if_display = if_alias if if_alias else if_name

            if_rx_mbit = cur_if_data["rx_speed_octets"] * 8 / 1000 / 1000
            if_tx_mbit = cur_if_data["tx_speed_octets"] * 8 / 1000 / 1000

            if self.include_interfaces and self.interfacesbandwidth:
                self._add_or_update_entity(
                    prefix + f"netif_{if_name}_curbw_out_mbit",
                    f"{if_display} bandwidth out",
                    round(if_tx_mbit, 2),
                    "Mbps",
                    "mdi:upload-network-outline",
                )
                self._add_or_update_entity(
                    prefix + f"netif_{if_name}_curbw_in_mbit",
                    f"{if_display} bandwidth in",
                    round(if_rx_mbit, 2),
                    "Mbps",
                    "mdi:download-network-outline",
                )

            if self.include_interfaces and self.interfacesoctets:
                self._add_or_update_entity(
                    prefix + f"netif_{if_name}_octets_out",
                    f"{if_display} octets out",
                    int(cur_if_data["tx_octets"]),
                    "octets",
                    "mdi:upload-network-outline",
                )
                self._add_or_update_entity(
                    prefix + f"netif_{if_name}_octets_in",
                    f"{if_display} octets in",
                    int(cur_if_data["rx_octets"]),
                    "octets",
                    "mdi:download-network-outline",
                )

        if self.include_cpu_and_ram:
            oids = (OID_CPUUSAGE, OID_RAMUSAGE)
            error_indication, oid_return = await async_snmp_getmulti(
                self.hass, self.target_ip, self.username, self.port, oids
            )
            if not error_indication:
                cpu_usage = int(oid_return[0][1].prettyPrint())
                ram_usage = int(oid_return[1][1].prettyPrint())
                self._add_or_update_entity(
                    prefix + "cpu_usage", "CPU usage", cpu_usage, "%", "mdi:memory"
                )
                self._add_or_update_entity(
                    prefix + "ram_usage", "RAM usage", ram_usage, "%", "mdi:memory"
                )

        if self.include_disk:
            oids = (OID_DISKUSAGE, OID_DISKCAPACITY)
            error_indication, oid_return = await async_snmp_getmulti(
                self.hass, self.target_ip, self.username, self.port, oids
            )
            if not error_indication:
                disk_usage = int(oid_return[0][1].prettyPrint())
                disk_capacity = int(oid_return[1][1].prettyPrint())
                disk_usagepct = int((disk_usage / disk_capacity) * 100)
                disk_attrs = {
                    "Disk capacity (MB)": disk_capacity,
                    "Disk usage (MB)": disk_usage,
                }
                self._add_or_update_entity(
                    prefix + "disk_usage",
                    "Disk usage",
                    disk_usagepct,
                    "%",
                    "mdi:database",
                    disk_attrs,
                )

        if self.include_sessions:
            error_indication, snmp_data = await async_snmp_getfromtable(
                self.hass,
                self.target_ip,
                self.username,
                self.port,
                OID_SESSIONCOUNT,
            )
            if not error_indication:
                sessioncount = 0
                for oid_entry in snmp_data:
                    for _oid, oid_value in oid_entry:
                        sessioncount += int(oid_value.prettyPrint())
                self._add_or_update_entity(
                    prefix + "sessions",
                    "Sessions",
                    sessioncount,
                    "sessions",
                    "mdi:format-list-bulleted-type",
                )

        if self.include_performanceslas and self.performance_slas:
            oids = (
                OID_PERFORMANCESLALINKNAME,
                OID_PERFORMANCESLALINKSTATE,
                OID_PERFORMANCESLALINKLATENCY,
                OID_PERFORMANCESLALINKJITTER,
                OID_PERFORMANCESLALINKPACKETLOSS,
                OID_PERFORMANCESLALINKBANDWIDTHIN,
                OID_PERFORMANCESLALINKBANDWIDTHOUT,
            )
            error_indication, snmp_data = await async_snmp_getmultifromtable(
                self.hass, self.target_ip, self.username, self.port, oids
            )
            if not error_indication:
                for (
                    sla_name,
                    sla_state,
                    sla_latency,
                    sla_jitter,
                    sla_packetloss,
                    sla_bandwidthin,
                    sla_bandwidthout,
                ) in snmp_data:
                    if sla_name[0].prettyPrint() not in self.performance_slas:
                        continue

                    sla_index = sla_name[0].prettyPrint().split(".")[-1]
                    sla_label = sla_name[1].prettyPrint()
                    sla_state_val = sla_state[1].prettyPrint()

                    if self.include_performanceslasstate:
                        self._add_or_update_entity(
                            prefix + f"sla_state_{sla_index}",
                            f"{sla_label} state",
                            PERFORMANCESLAS_STATE[sla_state_val],
                            "",
                            PERFORMANCESLAS_ICON[sla_state_val],
                        )

                    if self.include_performanceslaslinkmetrics:
                        self._add_or_update_entity(
                            prefix + f"sla_latency_{sla_index}",
                            f"{sla_label} latency",
                            int(float(sla_latency[1].prettyPrint())),
                            "ms",
                            "mdi:timeline-clock-outline",
                        )
                        self._add_or_update_entity(
                            prefix + f"sla_jitter_{sla_index}",
                            f"{sla_label} jitter",
                            int(float(sla_jitter[1].prettyPrint())),
                            "ms",
                            "mdi:timeline-clock-outline",
                        )
                        self._add_or_update_entity(
                            prefix + f"sla_packetloss_{sla_index}",
                            f"{sla_label} packet loss",
                            int(float(sla_packetloss[1].prettyPrint())),
                            "%",
                            "mdi:timeline-alert-outline",
                        )

                    if self.include_performanceslasbandwidthprobe:
                        self._add_or_update_entity(
                            prefix + f"sla_bandwidthin_{sla_index}",
                            f"{sla_label} probe bandwidth (in)",
                            int(sla_bandwidthin[1].prettyPrint()) / 1000,
                            "Mbps",
                            "mdi:download-network-outline",
                        )
                        self._add_or_update_entity(
                            prefix + f"sla_bandwidthout_{sla_index}",
                            f"{sla_label} probe bandwidth (out)",
                            int(sla_bandwidthout[1].prettyPrint()) / 1000,
                            "Mbps",
                            "mdi:upload-network-outline",
                        )
