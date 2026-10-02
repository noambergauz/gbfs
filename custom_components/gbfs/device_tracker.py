"""Device trackers for the closest GBFS vehicles."""

from __future__ import annotations

from homeassistant.components.device_tracker import (
    DOMAIN as DEVICE_TRACKER_DOMAIN,
    SourceType,
    TrackerEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BATTERY_LEVEL,
    ATTR_CURRENT_RANGE_METERS,
    ATTR_DISTANCE_M,
    ATTR_RENTAL_URI,
    ATTR_VEHICLE_ID,
)
from .coordinator import (
    GbfsConfigEntry,
    GbfsCoordinator,
    GbfsVehicle,
    configured_max_vehicles,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GbfsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one tracker per distance rank, closest first."""
    coordinator = entry.runtime_data
    slot_count = configured_max_vehicles(entry)
    unique_ids = {_slot_unique_id(entry.entry_id, rank) for rank in range(slot_count)}

    registry = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registered.domain == DEVICE_TRACKER_DOMAIN
            and registered.unique_id not in unique_ids
        ):
            registry.async_remove(registered.entity_id)

    async_add_entities(
        GbfsVehicleTracker(coordinator, rank) for rank in range(slot_count)
    )


class GbfsVehicleTracker(CoordinatorEntity[GbfsCoordinator], TrackerEntity):
    """Position of the vehicle currently at a given distance rank."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:scooter"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: GbfsCoordinator, rank: int) -> None:
        """Initialize the tracker for a zero-based distance rank."""
        super().__init__(coordinator)
        self._rank = rank
        self._attr_unique_id = _slot_unique_id(coordinator.entry.entry_id, rank)
        self._attr_name = f"Nearest {rank + 1}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Unavailable while fewer vehicles than this rank are in range."""
        return super().available and self._vehicle() is not None

    @property
    def latitude(self) -> float | None:
        """Return the latitude of the vehicle at this rank."""
        vehicle = self._vehicle()
        return None if vehicle is None else vehicle.latitude

    @property
    def longitude(self) -> float | None:
        """Return the longitude of the vehicle at this rank."""
        vehicle = self._vehicle()
        return None if vehicle is None else vehicle.longitude

    @property
    def extra_state_attributes(self) -> dict[str, int | float | str | None]:
        """Return details of the vehicle currently at this rank."""
        vehicle = self._vehicle()
        if vehicle is None:
            return {}
        return {
            ATTR_VEHICLE_ID: vehicle.vehicle_id,
            ATTR_DISTANCE_M: round(vehicle.distance_m),
            ATTR_BATTERY_LEVEL: vehicle.battery_level,
            ATTR_CURRENT_RANGE_METERS: vehicle.current_range_meters,
            ATTR_RENTAL_URI: vehicle.rental_uri,
        }

    def _vehicle(self) -> GbfsVehicle | None:
        vehicles = self.coordinator.data.vehicles
        if self._rank >= len(vehicles):
            return None
        return vehicles[self._rank]


def _slot_unique_id(entry_id: str, rank: int) -> str:
    return f"{entry_id}_nearest_{rank + 1}"
