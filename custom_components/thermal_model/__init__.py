"""YAML setup for the local thermal model integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import discovery

from .const import (
    CONF_ANALYSIS_INTERVAL_HOURS,
    CONF_BRIEF_VENTILATION_BELOW,
    CONF_DRYING_MIN_ABSOLUTE_HUMIDITY_GAIN,
    CONF_DRYING_MIN_HUMIDITY,
    CONF_HISTORY_DAYS,
    CONF_HISTORY_LOOKBACK_DAYS,
    CONF_HUMIDITY_SENSOR,
    CONF_ID,
    CONF_AREA_ID,
    CONF_NAME,
    CONF_MIN_ENTHALPY_GAIN,
    CONF_MIN_OUTDOOR_TEMPERATURE_RANGE,
    CONF_MAX_INDOOR_TEMPERATURE_CHANGE,
    CONF_MIN_TEMPERATURE,
    CONF_MIN_TEMPERATURE_GAIN,
    CONF_OUTDOOR,
    CONF_QUALITY,
    CONF_EXCLUSION_SENSORS,
    CONF_ENTITY_ID,
    CONF_ACTIVE_STATE,
    CONF_STATISTIC_ID,
    CONF_STATISTIC_TYPE,
    CONF_ABOVE,
    CONF_PROJECTED_HUMIDITY_MAX_ENTITY,
    CONF_PROJECTED_HUMIDITY_MIN_ENTITY,
    CONF_RAIN_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    CONF_VENTILATION,
    CONF_ZONES,
    DEFAULT_ANALYSIS_INTERVAL_HOURS,
    DEFAULT_BRIEF_VENTILATION_BELOW,
    DEFAULT_DRYING_MIN_ABSOLUTE_HUMIDITY_GAIN,
    DEFAULT_DRYING_MIN_HUMIDITY,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_HISTORY_LOOKBACK_DAYS,
    DEFAULT_MIN_OUTDOOR_TEMPERATURE_RANGE,
    DEFAULT_MAX_INDOOR_TEMPERATURE_CHANGE,
    DEFAULT_ACTIVE_STATE,
    DEFAULT_STATISTIC_TYPE,
    DEFAULT_ABOVE,
    DEFAULT_MIN_ENTHALPY_GAIN,
    DEFAULT_MIN_TEMPERATURE,
    DEFAULT_MIN_TEMPERATURE_GAIN,
    DOMAIN,
    SERVICE_ANALYZE,
)
from .model import ThermalModel

_LOGGER = logging.getLogger(__name__)

VENTILATION_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MIN_TEMPERATURE, default=DEFAULT_MIN_TEMPERATURE): vol.Coerce(float),
        vol.Optional(CONF_MIN_TEMPERATURE_GAIN, default=DEFAULT_MIN_TEMPERATURE_GAIN): vol.Coerce(float),
        vol.Optional(CONF_MIN_ENTHALPY_GAIN, default=DEFAULT_MIN_ENTHALPY_GAIN): vol.Coerce(float),
        vol.Optional(CONF_DRYING_MIN_HUMIDITY, default=DEFAULT_DRYING_MIN_HUMIDITY): vol.Coerce(float),
        vol.Optional(
            CONF_DRYING_MIN_ABSOLUTE_HUMIDITY_GAIN,
            default=DEFAULT_DRYING_MIN_ABSOLUTE_HUMIDITY_GAIN,
        ): vol.Coerce(float),
        vol.Optional(
            CONF_BRIEF_VENTILATION_BELOW,
            default=DEFAULT_BRIEF_VENTILATION_BELOW,
        ): vol.Coerce(float),
    }
)

EXCLUSION_SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_STATISTIC_ID): cv.string,
        vol.Optional(CONF_STATISTIC_TYPE, default=DEFAULT_STATISTIC_TYPE): vol.In(
            ["mean", "min", "max", "sum"]
        ),
        vol.Optional(CONF_ABOVE, default=DEFAULT_ABOVE): vol.Coerce(float),
        vol.Optional(CONF_ACTIVE_STATE, default=DEFAULT_ACTIVE_STATE): cv.string,
    }
)

QUALITY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MIN_OUTDOOR_TEMPERATURE_RANGE): vol.All(
            vol.Coerce(float), vol.Range(min=0.5, max=20)
        ),
        vol.Optional(CONF_MAX_INDOOR_TEMPERATURE_CHANGE): vol.All(
            vol.Coerce(float), vol.Range(min=0.2, max=10)
        ),
        vol.Optional(CONF_EXCLUSION_SENSORS): vol.All(
            cv.ensure_list, [EXCLUSION_SENSOR_SCHEMA]
        ),
    }
)

ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.slug,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_AREA_ID): cv.slug,
        vol.Required(CONF_TEMPERATURE_SENSOR): cv.entity_id,
        vol.Required(CONF_HUMIDITY_SENSOR): cv.entity_id,
        vol.Optional(CONF_PROJECTED_HUMIDITY_MIN_ENTITY): cv.entity_id,
        vol.Optional(CONF_PROJECTED_HUMIDITY_MAX_ENTITY): cv.entity_id,
        vol.Optional(CONF_VENTILATION, default={}): VENTILATION_SCHEMA,
        vol.Optional(CONF_QUALITY, default={}): QUALITY_SCHEMA,
    }
)

OUTDOOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TEMPERATURE_SENSOR): cv.entity_id,
        vol.Required(CONF_HUMIDITY_SENSOR): cv.entity_id,
        vol.Optional(CONF_RAIN_SENSOR): cv.entity_id,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_OUTDOOR): OUTDOOR_SCHEMA,
                vol.Required(CONF_ZONES): vol.All(cv.ensure_list, [ZONE_SCHEMA]),
                vol.Optional(CONF_HISTORY_DAYS, default=DEFAULT_HISTORY_DAYS): vol.All(
                    vol.Coerce(int), vol.Range(min=7, max=90)
                ),
                vol.Optional(
                    CONF_HISTORY_LOOKBACK_DAYS,
                    default=DEFAULT_HISTORY_LOOKBACK_DAYS,
                ): vol.All(vol.Coerce(int), vol.Range(min=14, max=365)),
                vol.Optional(
                    CONF_ANALYSIS_INTERVAL_HOURS,
                    default=DEFAULT_ANALYSIS_INTERVAL_HOURS,
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=6)),
                vol.Optional(CONF_QUALITY, default={}): QUALITY_SCHEMA,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the thermal model from YAML."""
    if DOMAIN not in config:
        return True

    model = ThermalModel(hass, config[DOMAIN])
    await model.async_load()
    hass.data[DOMAIN] = model

    async def handle_analyze(call: ServiceCall) -> None:
        zone_ids = call.data.get("zone_ids")
        await model.async_analyze(zone_ids)

    hass.services.async_register(
        DOMAIN,
        SERVICE_ANALYZE,
        handle_analyze,
        schema=vol.Schema({vol.Optional("zone_ids"): vol.All(cv.ensure_list, [cv.slug])}),
    )
    hass.async_create_task(discovery.async_load_platform(hass, "sensor", DOMAIN, {}, config))
    model.async_start()
    _LOGGER.info("Thermal model configured for %s zones", len(model.zones))
    return True
