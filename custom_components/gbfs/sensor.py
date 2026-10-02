"""Sensors for the GBFS integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ANDROID_STORE_URI,
    ATTR_BATTERY_LEVEL,
    ATTR_CLOSEST_VEHICLE_DISTANCE_M,
    ATTR_CURRENT_RANGE_METERS,
    ATTR_DOCKS_AVAILABLE,
    ATTR_IOS_STORE_URI,
    ATTR_LAST_UPDATED,
    ATTR_LAT,
    ATTR_LON,
    ATTR_OPERATOR_NAME,
    ATTR_RENTAL_URI,
    ATTR_SEARCH_RADIUS_M,
    ATTR_STATION_ID,
    ATTR_STATION_NAME,
    ATTR_VEHICLE_ID,
    ATTR_VEHICLES_AVAILABLE,
)
from .coordinator import GbfsConfigEntry, GbfsCoordinator, GbfsStation, GbfsVehicle


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GbfsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up count, distance, battery, and station sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            GbfsAvailableSensor(coordinator),
            GbfsDockedSensor(coordinator),
            GbfsNearestSensor(coordinator),
            GbfsNearestBatterySensor(coordinator),
            GbfsNearestStationSensor(coordinator),
        ]
    )


class GbfsAvailableSensor(CoordinatorEntity[GbfsCoordinator], SensorEntity):
    """Count of free vehicles inside the search radius."""

    _attr_has_entity_name = True
    _attr_translation_key = "available_nearby"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:bike"

    def __init__(self, coordinator: GbfsCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_available_nearby"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> int:
        """Return free vehicles inside the radius."""
        return len(self.coordinator.data.vehicles)

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        """Return search context for the count."""
        data = self.coordinator.data
        closest_distance: int | None = None
        if data.vehicles:
            closest_distance = round(data.vehicles[0].distance_m)
        return {
            ATTR_CLOSEST_VEHICLE_DISTANCE_M: closest_distance,
            ATTR_SEARCH_RADIUS_M: data.radius_meters,
            ATTR_OPERATOR_NAME: data.operator_name,
            ATTR_LAST_UPDATED: data.last_updated.isoformat(),
            ATTR_ANDROID_STORE_URI: data.android_store_uri,
            ATTR_IOS_STORE_URI: data.ios_store_uri,
        }


class GbfsNearestSensor(CoordinatorEntity[GbfsCoordinator], SensorEntity):
    """Distance to the closest available free-floating vehicle."""

    _attr_has_entity_name = True
    _attr_translation_key = "nearest_distance"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:map-marker-distance"

    def __init__(self, coordinator: GbfsCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_nearest_distance"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Unavailable when no free vehicle is inside the radius."""
        return super().available and _nearest_vehicle(self.coordinator) is not None

    @property
    def native_value(self) -> int | None:
        """Return the distance in meters."""
        vehicle = _nearest_vehicle(self.coordinator)
        if vehicle is None:
            return None
        return round(vehicle.distance_m)

    @property
    def extra_state_attributes(self) -> dict[str, int | float | str | None]:
        """Return details of the closest vehicle."""
        vehicle = _nearest_vehicle(self.coordinator)
        if vehicle is None:
            return {}
        return {
            ATTR_VEHICLE_ID: vehicle.vehicle_id,
            ATTR_BATTERY_LEVEL: vehicle.battery_level,
            ATTR_CURRENT_RANGE_METERS: vehicle.current_range_meters,
            ATTR_LAT: vehicle.latitude,
            ATTR_LON: vehicle.longitude,
            ATTR_RENTAL_URI: vehicle.rental_uri,
        }


class GbfsDockedSensor(CoordinatorEntity[GbfsCoordinator], SensorEntity):
    """Count of bikes available at stations inside the search radius."""

    _attr_has_entity_name = True
    _attr_translation_key = "docked_nearby"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:bike"

    def __init__(self, coordinator: GbfsCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_docked_nearby"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> int:
        """Return docked bikes inside the radius."""
        return self.coordinator.data.docked_available


class GbfsNearestBatterySensor(CoordinatorEntity[GbfsCoordinator], SensorEntity):
    """Battery of the closest free vehicle that passed the battery filter."""

    _attr_has_entity_name = True
    _attr_translation_key = "nearest_battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: GbfsCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_nearest_battery"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Unavailable when the closest vehicle has no battery reading."""
        vehicle = _nearest_vehicle(self.coordinator)
        if not super().available or vehicle is None:
            return False
        return vehicle.battery_level is not None

    @property
    def native_value(self) -> int | None:
        """Return the battery percentage."""
        vehicle = _nearest_vehicle(self.coordinator)
        if vehicle is None:
            return None
        return vehicle.battery_level


class GbfsNearestStationSensor(CoordinatorEntity[GbfsCoordinator], SensorEntity):
    """Distance to the closest renting station inside the search radius."""

    _attr_has_entity_name = True
    _attr_translation_key = "nearest_station"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:map-marker"

    def __init__(self, coordinator: GbfsCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_nearest_station"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Unavailable when no station is inside the radius."""
        return super().available and _nearest_station(self.coordinator) is not None

    @property
    def native_value(self) -> int | None:
        """Return the distance in meters."""
        station = _nearest_station(self.coordinator)
        if station is None:
            return None
        return round(station.distance_m)

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        """Return the closest station."""
        station = _nearest_station(self.coordinator)
        if station is None:
            return {}
        return {
            ATTR_STATION_ID: station.station_id,
            ATTR_STATION_NAME: station.name,
            ATTR_VEHICLES_AVAILABLE: station.vehicles_available,
            ATTR_DOCKS_AVAILABLE: station.docks_available,
        }


def _nearest_vehicle(coordinator: GbfsCoordinator) -> GbfsVehicle | None:
    if not coordinator.data.vehicles:
        return None
    return coordinator.data.vehicles[0]


def _nearest_station(coordinator: GbfsCoordinator) -> GbfsStation | None:
    if not coordinator.data.stations:
        return None
    return coordinator.data.stations[0]
