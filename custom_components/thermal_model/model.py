"""Calculations and historical analysis for the thermal model."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import math
from statistics import fmean
from typing import Any
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ANALYSIS_INTERVAL_HOURS,
    CONF_ACTIVE_STATE,
    CONF_BRIEF_VENTILATION_BELOW,
    CONF_DRYING_MIN_ABSOLUTE_HUMIDITY_GAIN,
    CONF_DRYING_MIN_HUMIDITY,
    CONF_HISTORY_DAYS,
    CONF_HISTORY_LOOKBACK_DAYS,
    CONF_HUMIDITY_SENSOR,
    CONF_ID,
    CONF_MIN_ENTHALPY_GAIN,
    CONF_MIN_OUTDOOR_TEMPERATURE_RANGE,
    CONF_MAX_INDOOR_TEMPERATURE_CHANGE,
    CONF_MIN_TEMPERATURE,
    CONF_MIN_TEMPERATURE_GAIN,
    CONF_NAME,
    CONF_LABELS,
    CONF_OUTDOOR,
    CONF_QUALITY,
    CONF_EXCLUSION_SENSORS,
    CONF_STATISTIC_ID,
    CONF_STATISTIC_TYPE,
    CONF_ABOVE,
    CONF_PROJECTED_HUMIDITY_MAX_ENTITY,
    CONF_PROJECTED_HUMIDITY_MIN_ENTITY,
    CONF_RAIN_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    CONF_VENTILATION,
    CONF_ZONES,
    DEFAULT_MAX_INDOOR_TEMPERATURE_CHANGE,
    DEFAULT_MIN_OUTDOOR_TEMPERATURE_RANGE,
    STORAGE_KEY,
    STORAGE_VERSION,
)


def _number(value: Any) -> float | None:
    """Return a finite state value, or None for unavailable values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _saturation_vapor_pressure(temperature: float) -> float:
    return 6.112 * math.exp((17.62 * temperature) / (243.12 + temperature))


def absolute_humidity(temperature: float, humidity: float) -> float:
    """Return absolute humidity in g/m3."""
    vapor_pressure = _saturation_vapor_pressure(temperature) * humidity / 100
    return 216.7 * vapor_pressure / (273.15 + temperature)


def moist_air_enthalpy(temperature: float, humidity: float) -> float:
    """Return moist-air enthalpy in kJ/kg of dry air at standard pressure."""
    vapor_pressure = _saturation_vapor_pressure(temperature) * humidity / 100
    humidity_ratio = 0.622 * vapor_pressure / (1013.25 - vapor_pressure)
    return 1.006 * temperature + humidity_ratio * (2501 + 1.86 * temperature)


def humidex(temperature: float, humidity: float) -> float:
    """Return humidex from the air temperature and relative humidity."""
    vapor_pressure = _saturation_vapor_pressure(temperature) * humidity / 100
    return temperature + (5 / 9) * (vapor_pressure - 10)


def projected_humidity(temperature: float, outdoor_absolute_humidity: float) -> float:
    """Return relative humidity after outdoor air reaches indoor temperature."""
    saturation_absolute_humidity = (
        216.7 * _saturation_vapor_pressure(temperature) / (273.15 + temperature)
    )
    return 100 * outdoor_absolute_humidity / saturation_absolute_humidity


class ThermalModel:
    """Central state and calculations for a configured building model."""

    def __init__(self, hass: HomeAssistant, configuration: dict[str, Any]) -> None:
        self.hass = hass
        self.configuration = configuration
        self.outdoor = configuration[CONF_OUTDOOR]
        self.zones = configuration[CONF_ZONES]
        self._listeners: list[Callable[[], None]] = []
        self._remove_tracker: Callable[[], None] | None = None
        self._analysis: dict[str, Any] = {}
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)

    async def async_load(self) -> None:
        """Load the latest completed historical analysis."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._analysis = stored

    def async_start(self) -> None:
        """Track all model inputs and update entities on each source update."""
        entity_ids = {
            self.outdoor[CONF_TEMPERATURE_SENSOR],
            self.outdoor[CONF_HUMIDITY_SENSOR],
        }
        if rain_sensor := self.outdoor.get(CONF_RAIN_SENSOR):
            entity_ids.add(rain_sensor)
        for zone in self.zones:
            entity_ids.add(zone[CONF_TEMPERATURE_SENSOR])
            entity_ids.add(zone[CONF_HUMIDITY_SENSOR])
            if entity_id := zone.get(CONF_PROJECTED_HUMIDITY_MIN_ENTITY):
                entity_ids.add(entity_id)
            if entity_id := zone.get(CONF_PROJECTED_HUMIDITY_MAX_ENTITY):
                entity_ids.add(entity_id)
        self._remove_tracker = async_track_state_change_event(
            self.hass, entity_ids, lambda _event: self._notify_listeners()
        )

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove_listener() -> None:
            self._listeners.remove(listener)

        return remove_listener

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            self.hass.add_job(listener)

    def _state_number(self, entity_id: str | None) -> float | None:
        state = self.hass.states.get(entity_id) if entity_id else None
        return _number(state.state) if state else None

    def label(self, key: str, default: str) -> str:
        """Return an optional consumer-provided display label."""
        return self.configuration.get(CONF_LABELS, {}).get(key, default)

    def _outdoor_snapshot(self) -> dict[str, float] | None:
        temperature = self._state_number(self.outdoor[CONF_TEMPERATURE_SENSOR])
        humidity = self._state_number(self.outdoor[CONF_HUMIDITY_SENSOR])
        if temperature is None or humidity is None or not 0 <= humidity <= 100:
            return None
        return {
            "temperature": temperature,
            "humidity": humidity,
            "absolute_humidity": absolute_humidity(temperature, humidity),
            "enthalpy": moist_air_enthalpy(temperature, humidity),
            "humidex": humidex(temperature, humidity),
        }

    def _zone_snapshot(self, zone: dict[str, Any]) -> dict[str, float] | None:
        temperature = self._state_number(zone[CONF_TEMPERATURE_SENSOR])
        humidity = self._state_number(zone[CONF_HUMIDITY_SENSOR])
        if temperature is None or humidity is None or not 0 <= humidity <= 100:
            return None
        return {
            "temperature": temperature,
            "humidity": humidity,
            "absolute_humidity": absolute_humidity(temperature, humidity),
            "enthalpy": moist_air_enthalpy(temperature, humidity),
            "humidex": humidex(temperature, humidity),
        }

    def _is_raining(self) -> bool:
        rain_sensor = self.outdoor.get(CONF_RAIN_SENSOR)
        state = self.hass.states.get(rain_sensor) if rain_sensor else None
        return state is not None and state.state == "on"

    def zone_value(self, zone: dict[str, Any], metric: str) -> float | str | None:
        """Return a live or analyzed metric for one zone."""
        snapshot = self._zone_snapshot(zone)
        outdoor = self._outdoor_snapshot()
        if metric in {"absolute_humidity", "enthalpy", "humidex"}:
            return round(snapshot[metric], 1) if snapshot else None
        if metric == "projected_humidity":
            if not snapshot or not outdoor:
                return None
            return round(projected_humidity(snapshot["temperature"], outdoor["absolute_humidity"]), 1)
        if metric == "enthalpy_gain":
            if not snapshot or not outdoor:
                return None
            return round(snapshot["enthalpy"] - outdoor["enthalpy"], 1)
        if metric == "ventilation_advice":
            return self._ventilation_advice(zone, snapshot, outdoor)
        zone_analysis = self._analysis.get("zones", {}).get(zone[CONF_ID], {})
        value = zone_analysis.get(metric)
        return round(value, 1) if isinstance(value, (int, float)) else None

    def zone_attributes(self, zone: dict[str, Any], metric: str) -> dict[str, Any]:
        if metric != "ventilation_advice":
            if metric in {"cooling_lag", "warming_lag", "thermal_inertia", "thermal_response"}:
                analysis = self._analysis.get("zones", {}).get(zone[CONF_ID], {})
                return {
                    "last_analysis": self._analysis.get("analyzed_at"),
                    "accepted_days": analysis.get("accepted_days", 0),
                    "required_days": self.configuration[CONF_HISTORY_DAYS],
                    "examined_days": analysis.get("examined_days", 0),
                    "rejected_days": analysis.get("rejected_days", 0),
                    "rejected_incomplete_days": analysis.get(
                        "rejected_incomplete_days", 0
                    ),
                    "rejected_low_outdoor_range_days": analysis.get(
                        "rejected_low_outdoor_range_days", 0
                    ),
                    "rejected_indoor_change_days": analysis.get(
                        "rejected_indoor_change_days", 0
                    ),
                    "rejected_exclusion_sensor_days": analysis.get(
                        "rejected_exclusion_sensor_days", 0
                    ),
                    "maximum_lookback_days": self.configuration[CONF_HISTORY_LOOKBACK_DAYS],
                }
            return {}
        snapshot = self._zone_snapshot(zone)
        outdoor = self._outdoor_snapshot()
        if not snapshot or not outdoor:
            return {}
        projected = projected_humidity(snapshot["temperature"], outdoor["absolute_humidity"])
        return {
            "enthalpy_difference": round(snapshot["enthalpy"] - outdoor["enthalpy"], 1),
            "absolute_humidity_difference": round(
                snapshot["absolute_humidity"] - outdoor["absolute_humidity"], 1
            ),
            "projected_humidity": round(projected, 1),
            "raining": self._is_raining(),
        }

    def global_value(self, metric: str) -> float | str | None:
        snapshots = [self._zone_snapshot(zone) for zone in self.zones]
        valid = [snapshot for snapshot in snapshots if snapshot]
        outdoor = self._outdoor_snapshot()
        if metric == "average_enthalpy":
            return round(fmean(snapshot["enthalpy"] for snapshot in valid), 1) if valid else None
        if metric == "average_enthalpy_gain":
            if not valid or not outdoor:
                return None
            return round(fmean(snapshot["enthalpy"] for snapshot in valid) - outdoor["enthalpy"], 1)
        if metric == "ventilation_summary":
            advice = [self._ventilation_advice(zone, self._zone_snapshot(zone), outdoor) for zone in self.zones]
            if any(value is None for value in advice):
                return None
            count = sum(value in {self.label('ventilate', 'Ventilate'), self.label('ventilate_briefly', 'Ventilate Briefly')} for value in advice)
            return self.label('keep_closed', 'Keep Closed') if count == 0 else f"{self.label('ventilate', 'Ventilate')} {count} room" + ("" if count == 1 else "s")
        return None

    def _ventilation_advice(
        self,
        zone: dict[str, Any],
        snapshot: dict[str, float] | None,
        outdoor: dict[str, float] | None,
    ) -> str | None:
        if not snapshot or not outdoor:
            return None
        ventilation = zone[CONF_VENTILATION]
        if self._is_raining():
            return self.label('keep_closed', 'Keep Closed')
        projected = projected_humidity(snapshot["temperature"], outdoor["absolute_humidity"])
        lower_bound = self._state_number(zone.get(CONF_PROJECTED_HUMIDITY_MIN_ENTITY))
        upper_bound = self._state_number(zone.get(CONF_PROJECTED_HUMIDITY_MAX_ENTITY))
        if lower_bound is not None and projected < lower_bound:
            return self.label('keep_closed', 'Keep Closed')
        if upper_bound is not None and projected > upper_bound:
            return self.label('keep_closed', 'Keep Closed')
        enthalpy_gain = snapshot["enthalpy"] - outdoor["enthalpy"]
        absolute_humidity_gain = snapshot["absolute_humidity"] - outdoor["absolute_humidity"]
        cooling = (
            snapshot["temperature"] >= ventilation[CONF_MIN_TEMPERATURE]
            and outdoor["temperature"] <= snapshot["temperature"] - ventilation[CONF_MIN_TEMPERATURE_GAIN]
            and enthalpy_gain >= ventilation[CONF_MIN_ENTHALPY_GAIN]
        )
        drying = (
            snapshot["humidity"] >= ventilation[CONF_DRYING_MIN_HUMIDITY]
            and absolute_humidity_gain >= ventilation[CONF_DRYING_MIN_ABSOLUTE_HUMIDITY_GAIN]
        )
        if drying and outdoor["temperature"] < ventilation[CONF_BRIEF_VENTILATION_BELOW]:
            return self.label('ventilate_briefly', 'Ventilate Briefly')
        return self.label('ventilate', 'Ventilate') if cooling or drying else self.label('keep_closed', 'Keep Closed')

    async def async_analyze(self, requested_zone_ids: list[str] | None = None) -> None:
        """Estimate thermal response from the most recent acceptable days."""
        zone_ids = set(requested_zone_ids or [zone[CONF_ID] for zone in self.zones])
        selected_zones = [zone for zone in self.zones if zone[CONF_ID] in zone_ids]
        if not selected_zones:
            return
        end = dt_util.utcnow()
        start = end - timedelta(days=self.configuration[CONF_HISTORY_LOOKBACK_DAYS])
        entity_ids = [self.outdoor[CONF_TEMPERATURE_SENSOR]] + [
            zone[CONF_TEMPERATURE_SENSOR] for zone in selected_zones
        ]
        for zone in selected_zones:
            quality = self._zone_quality(zone)
            entity_ids.extend(sensor[CONF_STATISTIC_ID] for sensor in quality[CONF_EXCLUSION_SENSORS])

        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        statistic_ids = list(dict.fromkeys(entity_ids))
        statistics = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            statistic_ids,
            "hour",
            None,
            {"mean", "min", "max", "sum"},
        )
        analyses = dict(self._analysis.get("zones", {}))
        outdoor_history = self._statistics_as_states(
            statistics.get(self.outdoor[CONF_TEMPERATURE_SENSOR], []), "mean"
        )
        for zone in selected_zones:
            quality = self._zone_quality(zone)
            analyses[zone[CONF_ID]] = self._analyze_zone(
                outdoor_history,
                self._statistics_as_states(
                    statistics.get(zone[CONF_TEMPERATURE_SENSOR], []), "mean"
                ),
                {
                    sensor[CONF_STATISTIC_ID]: self._statistics_as_states(
                        statistics.get(sensor[CONF_STATISTIC_ID], []),
                        sensor[CONF_STATISTIC_TYPE],
                    )
                    for sensor in quality[CONF_EXCLUSION_SENSORS]
                },
                quality,
                start,
                end,
            )
        self._analysis = {
            "analyzed_at": dt_util.utcnow().isoformat(),
            "zones": analyses,
        }
        await self._store.async_save(self._analysis)
        self._notify_listeners()

    @staticmethod
    def _statistics_as_states(rows: list[dict[str, Any]], value_key: str) -> list[Any]:
        """Adapt hourly long-term statistics to the recorder state interface."""
        return [
            SimpleNamespace(
                state=row[value_key],
                last_updated=datetime.fromtimestamp(row["start"] / 1000, dt_util.UTC),
            )
            for row in rows
            if row.get(value_key) is not None
        ]

    def _zone_quality(self, zone: dict[str, Any]) -> dict[str, Any]:
        """Merge global day-quality rules with optional zone-specific rules."""
        quality = dict(self.configuration[CONF_QUALITY])
        quality.update(zone.get(CONF_QUALITY, {}))
        quality.setdefault(
            CONF_MIN_OUTDOOR_TEMPERATURE_RANGE,
            DEFAULT_MIN_OUTDOOR_TEMPERATURE_RANGE,
        )
        quality.setdefault(
            CONF_MAX_INDOOR_TEMPERATURE_CHANGE,
            DEFAULT_MAX_INDOOR_TEMPERATURE_CHANGE,
        )
        quality.setdefault(CONF_EXCLUSION_SENSORS, [])
        return quality

    def _analyze_zone(
        self,
        outdoor_states: list[Any],
        indoor_states: list[Any],
        exclusion_history: dict[str, list[Any]],
        quality: dict[str, Any],
        start,
        end,
    ) -> dict[str, float | int | None]:
        interval = timedelta(hours=self.configuration[CONF_ANALYSIS_INTERVAL_HOURS])
        outdoor = self._sample_states(outdoor_states, start, end, interval)
        indoor = self._sample_states(indoor_states, start, end, interval)
        periods, quality_summary = self._select_acceptable_days(
            outdoor,
            indoor,
            exclusion_history,
            quality,
            start,
            end,
            interval,
        )
        if len(periods) < self.configuration[CONF_HISTORY_DAYS]:
            return quality_summary
        cooling = self._estimate_lag(periods, direction=-1)
        warming = self._estimate_lag(periods, direction=1)
        lags = [result["lag"] for result in (cooling, warming) if result]
        responses = [result["response"] for result in (cooling, warming) if result]
        return {
            **quality_summary,
            "cooling_lag": cooling["lag"] if cooling else None,
            "warming_lag": warming["lag"] if warming else None,
            "thermal_inertia": fmean(lags) if lags else None,
            "thermal_response": 100 * fmean(responses) if responses else None,
        }

    @staticmethod
    def _sample_states(states: list[Any], start, end, interval: timedelta) -> list[float | None]:
        samples: list[float | None] = []
        index = 0
        current: float | None = None
        ordered = sorted(states, key=lambda state: state.last_updated)
        timestamp = start
        while timestamp <= end:
            while index < len(ordered) and ordered[index].last_updated <= timestamp:
                current = _number(ordered[index].state)
                index += 1
            samples.append(current)
            timestamp += interval
        return samples

    def _select_acceptable_days(
        self,
        outdoor: list[float | None],
        indoor: list[float | None],
        exclusion_history: dict[str, list[Any]],
        quality: dict[str, Any],
        start,
        end,
        interval: timedelta,
    ) -> tuple[list[tuple[list[float], list[float]]], dict[str, int]]:
        """Return recent full days that satisfy the configured quality rules."""
        samples_by_day: dict[date, list[tuple[float | None, float | None]]] = {}
        timestamp = start
        for outdoor_value, indoor_value in zip(outdoor, indoor, strict=True):
            local_day = dt_util.as_local(timestamp).date()
            samples_by_day.setdefault(local_day, []).append((outdoor_value, indoor_value))
            timestamp += interval

        expected_samples = int(timedelta(days=1) / interval)
        accepted: list[tuple[list[float], list[float]]] = []
        examined = 0
        rejected = 0
        rejected_incomplete = 0
        rejected_low_outdoor_range = 0
        rejected_indoor_change = 0
        rejected_exclusion_sensor = 0
        for day in sorted(samples_by_day, reverse=True):
            samples = samples_by_day[day]
            if len(samples) < expected_samples:
                continue
            examined += 1
            outdoor_values = [sample[0] for sample in samples]
            indoor_values = [sample[1] for sample in samples]
            if None in outdoor_values or None in indoor_values:
                rejected += 1
                rejected_incomplete += 1
                continue
            numeric_outdoor = [value for value in outdoor_values if value is not None]
            numeric_indoor = [value for value in indoor_values if value is not None]
            if (
                max(numeric_outdoor) - min(numeric_outdoor)
                < quality[CONF_MIN_OUTDOOR_TEMPERATURE_RANGE]
            ):
                rejected += 1
                rejected_low_outdoor_range += 1
                continue
            if any(
                abs(after - before) > quality[CONF_MAX_INDOOR_TEMPERATURE_CHANGE]
                for before, after in zip(numeric_indoor, numeric_indoor[1:], strict=False)
            ):
                rejected += 1
                rejected_indoor_change += 1
                continue
            if self._has_exclusion_state(day, exclusion_history, quality[CONF_EXCLUSION_SENSORS]):
                rejected += 1
                rejected_exclusion_sensor += 1
                continue
            accepted.append((numeric_outdoor, numeric_indoor))
            if len(accepted) == self.configuration[CONF_HISTORY_DAYS]:
                break
        return accepted, {
            "accepted_days": len(accepted),
            "examined_days": examined,
            "rejected_days": rejected,
            "rejected_incomplete_days": rejected_incomplete,
            "rejected_low_outdoor_range_days": rejected_low_outdoor_range,
            "rejected_indoor_change_days": rejected_indoor_change,
            "rejected_exclusion_sensor_days": rejected_exclusion_sensor,
        }

    @staticmethod
    def _has_exclusion_state(
        day: date,
        exclusion_history: dict[str, list[Any]],
        exclusion_sensors: list[dict[str, str]],
    ) -> bool:
        """Return whether a configured exclusion sensor was active during a day."""
        for sensor in exclusion_sensors:
            active_state = sensor[CONF_ACTIVE_STATE]
            active = False
            for state in sorted(
                exclusion_history[sensor[CONF_STATISTIC_ID]], key=lambda item: item.last_updated
            ):
                state_day = dt_util.as_local(state.last_updated).date()
                if state_day > day:
                    break
                if state_day == day and active:
                    return True
                active = _number(state.state) is not None and _number(state.state) > sensor[CONF_ABOVE]
                if state_day == day and active:
                    return True
            if active:
                return True
        return False

    @staticmethod
    def _estimate_lag(
        periods: list[tuple[list[float], list[float]]], direction: int
    ) -> dict[str, float] | None:
        candidates: list[dict[str, float]] = []
        for lag in range(13):
            outside_changes: list[float] = []
            inside_changes: list[float] = []
            for outdoor, indoor in periods:
                for index in range(1, len(outdoor) - lag):
                    outdoor_change = outdoor[index] - outdoor[index - 1]
                    indoor_change = indoor[index + lag] - indoor[index + lag - 1]
                    if direction * outdoor_change < 0.2:
                        continue
                    outside_changes.append(outdoor_change)
                    inside_changes.append(indoor_change)
            if len(outside_changes) < 24:
                continue
            correlation = ThermalModel._correlation(outside_changes, inside_changes)
            if correlation is None:
                continue
            response = fmean(
                abs(indoor_change) / abs(outdoor_change)
                for outdoor_change, indoor_change in zip(outside_changes, inside_changes, strict=True)
                if abs(outdoor_change) >= 0.2
            )
            candidates.append(
                {
                    "lag": float(lag),
                    "response": min(response, 2.0),
                    "score": correlation,
                }
            )
        if not candidates:
            return None
        best = max(candidates, key=lambda result: result["score"])
        return best if best["score"] > 0 else None

    @staticmethod
    def _correlation(first: list[float], second: list[float]) -> float | None:
        mean_first = fmean(first)
        mean_second = fmean(second)
        numerator = sum((a - mean_first) * (b - mean_second) for a, b in zip(first, second, strict=True))
        first_variance = sum((value - mean_first) ** 2 for value in first)
        second_variance = sum((value - mean_second) ** 2 for value in second)
        denominator = math.sqrt(first_variance * second_variance)
        return numerator / denominator if denominator else None
