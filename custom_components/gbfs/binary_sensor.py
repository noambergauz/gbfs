"""Binary sensors for the GBFS integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import GbfsConfigEntry, GbfsCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GbfsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the usable-vehicle binary sensor."""
    async_add_entities([GbfsUsableNearbyBinarySensor(entry.runtime_data)])


class GbfsUsableNearbyBinarySensor(
    CoordinatorEntity[GbfsCoordinator], BinarySensorEntity
):
    """On when a free vehicle inside the radius passed the battery filter."""

    _attr_has_entity_name = True
    _attr_translation_key = "usable_nearby"
    _attr_icon = "mdi:scooter"

    def __init__(self, coordinator: GbfsCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_usable_nearby"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        """Return true when at least one usable vehicle is in range."""
        return bool(self.coordinator.data.vehicles)
