"""Sensor platform for the local thermal model."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_AREA_ID, CONF_ID, CONF_NAME, DOMAIN
from .model import ThermalModel


@dataclass(frozen=True, kw_only=True)
class MetricDescription:
    key: str
    label: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    icon: str | None = None


ZONE_METRICS = (
    MetricDescription(key="absolute_humidity", label="Absolute Humidity", unit="g/m³", state_class=SensorStateClass.MEASUREMENT, icon="mdi:water"),
    MetricDescription(key="enthalpy", label="Specific Enthalpy", unit="kJ/kg", state_class=SensorStateClass.MEASUREMENT, icon="mdi:weather-windy"),
    MetricDescription(key="humidex", label="Humidex", unit=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer-water"),
    MetricDescription(key="projected_humidity", label="Projected Humidity", unit="%", device_class=SensorDeviceClass.HUMIDITY, state_class=SensorStateClass.MEASUREMENT, icon="mdi:water-sync"),
    MetricDescription(key="enthalpy_gain", label="Enthalpy Gain", unit="kJ/kg", state_class=SensorStateClass.MEASUREMENT, icon="mdi:weather-windy"),
    MetricDescription(key="ventilation_advice", label="Ventilation Advice", icon="mdi:window-open-variant"),
    MetricDescription(key="comfort_score", label="Comfort Score", unit="%", state_class=SensorStateClass.MEASUREMENT, icon="mdi:home-heart"),
    MetricDescription(key="comfort_stability", label="Comfort Stability", state_class=SensorStateClass.MEASUREMENT, icon="mdi:chart-bell-curve"),
    MetricDescription(key="temperature_variation_rate", label="Temperature Variation Rate", unit="°C/h", state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer-lines"),
    MetricDescription(key="heating_responsiveness", label="Heating Responsiveness", unit="%/h", state_class=SensorStateClass.MEASUREMENT, icon="mdi:weather-sunset-up"),
    MetricDescription(key="cooling_responsiveness", label="Cooling Responsiveness", unit="%/h", state_class=SensorStateClass.MEASUREMENT, icon="mdi:weather-sunset-down"),
    MetricDescription(key="night_cooling_effectiveness", label="Night Cooling Effectiveness", unit="%/h", state_class=SensorStateClass.MEASUREMENT, icon="mdi:weather-night"),
    MetricDescription(key="comfort_retention_score", label="Comfort Retention Score", unit="%", state_class=SensorStateClass.MEASUREMENT, icon="mdi:home-thermometer"),
)

GLOBAL_METRICS = (
    MetricDescription(key="average_enthalpy", label="Average Indoor Enthalpy", unit="kJ/kg", state_class=SensorStateClass.MEASUREMENT, icon="mdi:weather-windy"),
    MetricDescription(key="average_enthalpy_gain", label="Average Indoor Enthalpy Gain", unit="kJ/kg", state_class=SensorStateClass.MEASUREMENT, icon="mdi:weather-windy"),
    MetricDescription(key="ventilation_summary", label="Ventilation Summary", icon="mdi:window-open-variant"),
)


async def async_setup_platform(
    hass: HomeAssistant,
    _config: dict[str, Any],
    async_add_entities,
    _discovery_info: dict[str, Any] | None = None,
) -> None:
    """Set up sensors from YAML configuration."""
    model: ThermalModel = hass.data[DOMAIN]
    entities: list[SensorEntity] = [
        GlobalMetricSensor(model, description) for description in GLOBAL_METRICS
    ]
    for zone in model.zones:
        entities.extend(ZoneMetricSensor(model, zone, description) for description in ZONE_METRICS)
    async_add_entities(entities)


class ZoneMetricSensor(SensorEntity):
    """Expose a metric for a single thermal zone."""

    _attr_has_entity_name = False

    def __init__(self, model: ThermalModel, zone: dict[str, Any], description: MetricDescription) -> None:
        self.model = model
        self.zone = zone
        self.description = description
        self._attr_unique_id = f"{DOMAIN}_{zone[CONF_ID]}_{description.key}"
        self._attr_name = f"{self.model.label('prefix', 'Thermal Model')} {zone[CONF_NAME]} {self.model.label(description.key, description.label)}"
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_icon = description.icon
        self._remove_listener = None

    async def async_added_to_hass(self) -> None:
        self.hass.async_create_task(self._async_assign_area())
        self._remove_listener = self.model.add_listener(self.async_schedule_update_ha_state)

    async def _async_assign_area(self) -> None:
        await asyncio.sleep(0)
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        if entry and entry.area_id != self.zone[CONF_AREA_ID]:
            registry.async_update_entity(self.entity_id, area_id=self.zone[CONF_AREA_ID])

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()

    @property
    def native_value(self) -> float | str | None:
        return self.model.zone_value(self.zone, self.description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.model.zone_attributes(self.zone, self.description.key)


class GlobalMetricSensor(SensorEntity):
    """Expose a building-wide metric."""

    _attr_has_entity_name = False

    def __init__(self, model: ThermalModel, description: MetricDescription) -> None:
        self.model = model
        self.description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"
        self._attr_name = f"{self.model.label('prefix', 'Thermal Model')} {self.model.label(description.key, description.label)}"
        self._attr_native_unit_of_measurement = description.unit
        self._attr_state_class = description.state_class
        self._attr_icon = description.icon
        self._remove_listener = None

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.model.add_listener(self.async_schedule_update_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()

    @property
    def native_value(self) -> float | str | None:
        return self.model.global_value(self.description.key)
