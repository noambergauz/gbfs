"""Config flow for the GBFS integration."""

from __future__ import annotations

from collections.abc import Mapping

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    PERCENTAGE,
    UnitOfLength,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_GBFS_URL,
    CONF_MAX_VEHICLES,
    CONF_MIN_BATTERY,
    CONF_RADIUS,
    DEFAULT_MAX_VEHICLES,
    DEFAULT_MIN_BATTERY_PERCENT,
    DEFAULT_RADIUS_METERS,
    DOMAIN,
)
from .coordinator import (
    GbfsConnectionError,
    GbfsInvalidFeedError,
    GbfsNoVehicleFeedError,
    async_validate_discovery,
    normalize_gbfs_url,
    required_float,
    required_int,
)


class GbfsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a GBFS feed."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GbfsOptionsFlow:
        """Create the options flow for an existing entry."""
        return GbfsOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> ConfigFlowResult:
        """Collect a GBFS discovery URL and the search location."""
        errors: dict[str, str] = {}
        if user_input is not None:
            submitted = dict(user_input)
            raw_url = submitted.get(CONF_GBFS_URL)
            if isinstance(raw_url, str):
                submitted[CONF_GBFS_URL] = raw_url.strip()
            errors = await self._async_validate_feed(submitted)
            if not errors:
                name = str(submitted[CONF_NAME]).strip()
                url = str(submitted[CONF_GBFS_URL])
                await self.async_set_unique_id(normalize_gbfs_url(url))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={CONF_GBFS_URL: url},
                    options=_location_options(submitted),
                )
            user_input = submitted

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _feed_schema(),
                self._suggested_feed(user_input),
            ),
            errors=errors,
        )

    async def _async_validate_feed(
        self, user_input: Mapping[str, object]
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        name = user_input.get(CONF_NAME)
        if not isinstance(name, str) or not name.strip():
            errors[CONF_NAME] = "invalid_name"

        url = user_input.get(CONF_GBFS_URL)
        if not isinstance(url, str) or not url.strip():
            errors[CONF_GBFS_URL] = "missing_url"
            return errors

        try:
            session = async_get_clientsession(self.hass)
            await async_validate_discovery(session, url)
        except GbfsConnectionError:
            errors[CONF_GBFS_URL] = "cannot_connect"
        except GbfsInvalidFeedError:
            errors[CONF_GBFS_URL] = "invalid_gbfs"
        except GbfsNoVehicleFeedError:
            errors[CONF_GBFS_URL] = "no_vehicle_feed"
        return errors

    def _suggested_feed(
        self, user_input: Mapping[str, object] | None
    ) -> dict[str, object]:
        suggested: dict[str, object] = {
            CONF_NAME: "",
            CONF_GBFS_URL: "",
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
            CONF_RADIUS: DEFAULT_RADIUS_METERS,
            CONF_MAX_VEHICLES: DEFAULT_MAX_VEHICLES,
            CONF_MIN_BATTERY: DEFAULT_MIN_BATTERY_PERCENT,
        }
        if user_input is not None:
            suggested.update(dict(user_input))
        return suggested


class GbfsOptionsFlow(OptionsFlow):
    """Edit the search location for an existing feed."""

    async def async_step_init(
        self, user_input: dict[str, object] | None = None
    ) -> ConfigFlowResult:
        """Manage the search area, tracker cap, and minimum battery."""
        if user_input is not None:
            return self.async_create_entry(data=_location_options(user_input))

        options = self.config_entry.options
        suggested: dict[str, object] = {
            CONF_LATITUDE: options.get(CONF_LATITUDE, self.hass.config.latitude),
            CONF_LONGITUDE: options.get(CONF_LONGITUDE, self.hass.config.longitude),
            CONF_RADIUS: options.get(CONF_RADIUS, DEFAULT_RADIUS_METERS),
            CONF_MAX_VEHICLES: options.get(CONF_MAX_VEHICLES, DEFAULT_MAX_VEHICLES),
            CONF_MIN_BATTERY: options.get(CONF_MIN_BATTERY, DEFAULT_MIN_BATTERY_PERCENT),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _location_schema(), suggested
            ),
        )


def _location_options(user_input: Mapping[str, object]) -> dict[str, float | int]:
    return {
        CONF_LATITUDE: required_float(user_input[CONF_LATITUDE]),
        CONF_LONGITUDE: required_float(user_input[CONF_LONGITUDE]),
        CONF_RADIUS: required_int(user_input[CONF_RADIUS]),
        CONF_MAX_VEHICLES: required_int(user_input[CONF_MAX_VEHICLES]),
        CONF_MIN_BATTERY: required_int(user_input[CONF_MIN_BATTERY]),
    }


def _feed_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME): TextSelector(),
            vol.Required(CONF_GBFS_URL): TextSelector(),
            **_location_fields(),
        }
    )


def _location_schema() -> vol.Schema:
    return vol.Schema(_location_fields())


def _location_fields() -> dict[vol.Marker, NumberSelector]:
    return {
        vol.Required(CONF_LATITUDE): _number_selector(
            minimum=-90,
            maximum=90,
            step="any",
        ),
        vol.Required(CONF_LONGITUDE): _number_selector(
            minimum=-180,
            maximum=180,
            step="any",
        ),
        vol.Required(CONF_RADIUS): _number_selector(
            minimum=1,
            maximum=100_000,
            step=1,
            unit=UnitOfLength.METERS,
        ),
        vol.Required(CONF_MAX_VEHICLES): _number_selector(
            minimum=1,
            maximum=50,
            step=1,
        ),
        vol.Required(CONF_MIN_BATTERY): _number_selector(
            minimum=0,
            maximum=100,
            step=1,
            unit=PERCENTAGE,
        ),
    }


def _number_selector(
    *,
    minimum: float,
    maximum: float,
    step: float | str,
    unit: str | None = None,
) -> NumberSelector:
    config = NumberSelectorConfig(
        min=minimum,
        max=maximum,
        step=step,
        mode=NumberSelectorMode.BOX,
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)
