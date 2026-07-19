# Thermal Model

Thermal Model is a YAML-configured Home Assistant custom integration for indoor climate analysis. It publishes live
psychrometric measurements and ventilation advice for each configured zone, then derives longer-term thermal metrics
from Recorder statistics.

## Features

- Live absolute humidity, specific enthalpy, humidex, projected humidity, and enthalpy gain.
- Per-zone ventilation advice based on temperature, moisture, enthalpy, rain, and optional projected-humidity limits.
- A live, season-adaptive comfort score.
- Three horizons: live comfort, recent operational behaviour, and long-term building response.
- A building-wide average enthalpy, enthalpy gain, and ventilation summary.
- Localized entity names and recommendations through configurable labels.

## Requirements

- Home Assistant with the [`recorder`](https://www.home-assistant.io/integrations/recorder/) integration enabled.
- Temperature and relative-humidity sensors for outdoors and every managed zone.
- Enough Recorder long-term statistics for the configured operational and building windows. Sensors must provide usable
  hourly mean statistics.
- A configured Home Assistant location: night-cooling analysis uses local sunset and sunrise.

## Installation

1. Copy `custom_components/thermal_model` into your Home Assistant configuration directory:

   ```text
   config/custom_components/thermal_model
   ```

2. Restart Home Assistant.
3. Add a `thermal_model:` section to `configuration.yaml` or to an included package.
4. Restart Home Assistant again after changing the YAML configuration.

This integration currently has no config flow and is installed manually. It does not require HACS.

## Configuration

The smallest useful configuration defines one outdoor source and one zone:

```yaml
thermal_model:
  outdoor:
    temperature_sensor: sensor.outdoor_temperature
    humidity_sensor: sensor.outdoor_humidity
    rain_sensor: binary_sensor.rain

  zones:
    - id: living_room
      name: Living Room
      area_id: living_room
      temperature_sensor: sensor.living_room_temperature
      humidity_sensor: sensor.living_room_humidity
```

`id` and `area_id` must be Home Assistant slugs. The area must already exist. `name` is visible in the generated entity
names and can use any language.

### Full example

```yaml
thermal_model:
  outdoor:
    temperature_sensor: sensor.outdoor_temperature
    humidity_sensor: sensor.outdoor_humidity
    rain_sensor: binary_sensor.rain

  # Recent operation: ventilation and comfort behaviour.
  operational:
    history_days: 14
    history_lookback_days: 35

  # Long-term building response.
  building:
    history_days: 42
    history_lookback_days: 180
  analysis_interval_hours: 1

  night_cooling:
    # A score of 5 means 20% of the indoor/outdoor gap is reduced each hour.
    target_gap_reduction_per_hour: 0.2

  # Default data-quality rules for every zone.
  quality:
    min_outdoor_temperature_range: 3.0
    max_indoor_temperature_change: 2.0
    exclusion_sensors: [ ]

  zones:
    - id: living_room
      name: Living Room
      area_id: living_room
      temperature_sensor: sensor.living_room_temperature
      humidity_sensor: sensor.living_room_humidity
      comfort:
        target_temperature: 21
        temperature_tolerance: 1
      ventilation:
        min_temperature: 24
        min_temperature_gain: 0.8
        min_enthalpy_gain: 1.5
        drying_min_humidity: 60
        drying_min_absolute_humidity_gain: 1.0
        brief_ventilation_below: 12

    - id: music_room
      name: Music Room
      area_id: music_room
      temperature_sensor: sensor.music_room_temperature
      humidity_sensor: sensor.music_room_humidity
      projected_humidity_min_entity: input_number.music_room_humidity_minimum
      projected_humidity_max_entity: input_number.music_room_humidity_maximum
      night_cooling:
        # Keep cooling scores comparable when projected humidity is not a constraint.
        ignore_projected_humidity: true
      quality:
        max_indoor_temperature_change: 1.0
```

### Options

| Option                                  | Default | Description                                                                              |
|-----------------------------------------|---------|------------------------------------------------------------------------------------------|
| `operational.history_days`              | `14`    | Recent acceptable days required for operational metrics. Range: 7–90.                    |
| `operational.history_lookback_days`     | `35`    | Recorder period searched for operational days. Range: 14–365.                            |
| `building.history_days`                 | `42`    | Acceptable days required for building metrics. Range: 7–90.                              |
| `building.history_lookback_days`        | `120`   | Recorder period searched for building days. Range: 14–365.                               |
| `zones[].night_cooling.ignore_projected_humidity` | `false` | Ignore projected-humidity limits when selecting periods recommended for cooling. |
| `analysis_interval_hours`               | `1`     | Sampling period for historical analysis. Range: 1–6 hours.                               |
| `night_cooling.target_gap_reduction_per_hour` | `0.2` | Fraction of the indoor/outdoor gap reduced per hour for a night-cooling score of `5`. |
| `quality.min_outdoor_temperature_range` | `3.0`   | Minimum daily outdoor-temperature range, in °C, for a day to be accepted.                |
| `quality.max_indoor_temperature_change` | `2.0`   | Maximum change between two samples, in °C, before a day is rejected.                     |
| `comfort.target_temperature`            | `21`    | Reference temperature used for comfort stability.                                        |
| `comfort.temperature_tolerance`         | `1`     | Maximum typical temperature movement, in °C, before stability reaches zero.              |

Zone `quality` settings override the global values for that zone. `rain_sensor` and both projected-humidity-limit
entities are optional.

### Excluding unsuitable days

Use `exclusion_sensors` when a known condition makes thermal analysis unrepresentative, such as a window-open indicator
or HVAC mode. Each entry references a Recorder statistic ID and rejects the day when the selected statistic is greater
than `above`.

```yaml
thermal_model:
  quality:
    exclusion_sensors:
      - statistic_id: sensor.window_open_ratio
        statistic_type: max
        above: 0
```

Supported `statistic_type` values are `mean`, `min`, `max`, and `sum`.

### Localized labels

The integration defaults to English. Override only the labels that should be visible differently:

```yaml
thermal_model:
  labels:
    prefix: Modele thermique
    ventilation_advice: Recommandation d'aeration
    ventilate: Aerer
    ventilate_briefly: Aerer brievement
    keep_closed: Garder ferme
```

Label changes affect entity display names, not their stable entity IDs or unique IDs.

## Entities and metrics

Each zone receives the following sensors. Live metrics use current sensor states; operational metrics use the recent
window; building metrics use the long-term window.

| Metric                           | Meaning                                                                                                                       |
|----------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| Absolute Humidity                | Water-vapor mass per cubic metre of air.                                                                                      |
| Specific Enthalpy                | Total heat and moisture content of air, in kJ/kg.                                                                             |
| Humidex                          | Apparent temperature derived from temperature and humidity.                                                                   |
| Projected Humidity               | Estimated relative humidity if indoor air is replaced by outdoor air at the current indoor temperature.                       |
| Enthalpy Gain                    | Indoor specific enthalpy minus outdoor specific enthalpy. A positive value means outdoor air has lower enthalpy.              |
| Ventilation Advice               | `Ventilate`, `Ventilate Briefly`, or `Keep Closed`.                                                                           |
| Comfort Score                    | Live score from `0` to `5`: `5` inside the comfort band, then decreasing linearly to `0` at 4 °C outside it.                  |
| Comfort Temperature Deviation    | Current absolute distance outside the comfort band, in °C. Zero means that the room is inside the band.                      |
| Comfort Stability                | Recent score from `0` to `5` based on the typical hourly indoor-temperature movement.                                         |
| Temperature Variation Rate       | Recent mean absolute indoor-temperature variation between hourly samples, in °C/h.                                            |
| Night Cooling Effectiveness      | Recent score from `0` to `5`; `5` reaches the configured gap-reduction target each hour.                                    |
| Night Cooling Rate               | Recent mean indoor temperature decrease during the night-cooling window, in °C/h.                                             |
| Heating / Cooling Responsiveness | Long-term indoor response relative to outdoor warming or cooling.                                                             |
| Comfort Retention Score          | Long-term inverse of the average thermal response; higher means indoor temperature changes less relative to outdoor changes. |

The integration also publishes Average Indoor Enthalpy, Average Indoor Enthalpy Gain, and Ventilation Summary for all
configured zones.

Historical metrics remain unavailable until enough acceptable days have been found. Their attributes report how many
days were accepted, examined, and rejected, which is useful when tuning quality rules.

## Running the analysis

Call `thermal_model.analyze` to refresh historical metrics. With no data, all configured zones are analysed. `zone_ids`
optionally restricts the run to one or more technical zone IDs.

```yaml
service: thermal_model.analyze
data:
  zone_ids:
    - living_room
```

Run it on a schedule after the Recorder has produced the previous day’s statistics:

```yaml
automation:
  - alias: Analyze thermal model daily
    triggers:
      - trigger: time
        at: "03:15:00"
    actions:
      - action: thermal_model.analyze
    mode: single
```

## Troubleshooting

- **Live metrics are `unknown`:** verify that the configured temperature and humidity entities have current numeric
  states.
- **Historical metrics are `unknown`:** check the entity history and long-term statistics, then inspect the metric
  attributes for accepted and rejected day counts. Adjust the corresponding `operational` or `building` window only
  when the retained days are genuinely representative.
- **Night cooling is `unknown`:** ensure Home Assistant has a valid location and that enough consecutive acceptable days
  exist to cover the night period.
- **No new entities after installation:** confirm the directory is exactly `custom_components/thermal_model`, check the
  Home Assistant logs, and restart after any Python or YAML change.

## Development

The integration uses only Home Assistant core APIs and Python standard-library calculations. Run a syntax check before
committing:

```bash
python3 -m py_compile custom_components/thermal_model/*.py
```
