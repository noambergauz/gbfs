"""GBFS discovery, parsing, and periodic updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeAlias
from urllib.parse import urljoin, urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import utcnow
from homeassistant.util.location import distance as distance_m

from .const import (
    CONF_GBFS_URL,
    CONF_MAX_VEHICLES,
    CONF_MIN_BATTERY,
    CONF_RADIUS,
    DEFAULT_MAX_VEHICLES,
    DEFAULT_MIN_BATTERY_PERCENT,
    DEFAULT_RADIUS_METERS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MIN_SCAN_INTERVAL_SECONDS,
    FEED_FREE_BIKE_STATUS,
    FEED_STATION_INFORMATION,
    FEED_STATION_STATUS,
    FEED_SYSTEM_INFORMATION,
    FEED_VEHICLE_STATUS,
    REQUEST_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class GbfsError(Exception):
    """Base error for GBFS fetch and parse failures."""


class GbfsConnectionError(GbfsError):
    """The feed could not be reached."""


class GbfsInvalidFeedError(GbfsError):
    """The payload is not a usable GBFS document."""


class GbfsNoVehicleFeedError(GbfsError):
    """Discovery published neither vehicles nor stations."""


@dataclass(frozen=True, slots=True)
class GbfsVehicle:
    """One available free-floating vehicle inside the search radius."""

    vehicle_id: str
    latitude: float
    longitude: float
    distance_m: float
    battery_level: int | None
    current_range_meters: float | None
    rental_uri: str | None


@dataclass(frozen=True, slots=True)
class GbfsStation:
    """One renting station inside the search radius."""

    station_id: str
    name: str | None
    latitude: float
    longitude: float
    distance_m: float
    vehicles_available: int
    docks_available: int | None


@dataclass(frozen=True, slots=True)
class GbfsData:
    """Snapshot consumed by sensors and device trackers."""

    operator_name: str | None
    android_store_uri: str | None
    ios_store_uri: str | None
    vehicles: tuple[GbfsVehicle, ...]
    stations: tuple[GbfsStation, ...]
    radius_meters: int
    min_battery_percent: int
    last_updated: datetime

    @property
    def docked_available(self) -> int:
        """Return bikes available at stations inside the radius."""
        return sum(station.vehicles_available for station in self.stations)


class GbfsCoordinator(DataUpdateCoordinator[GbfsData]):
    """Poll one GBFS system and keep vehicles sorted by distance."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator for a config entry."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device shared by this feed's entities."""
        info = DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            model="GBFS",
        )
        if self.data.operator_name:
            info["manufacturer"] = self.data.operator_name
        store_url = self.data.android_store_uri or self.data.ios_store_uri
        if store_url is not None:
            info["configuration_url"] = store_url
        return info

    async def _async_update_data(self) -> GbfsData:
        """Fetch discovery and child feeds, then filter to the search radius."""
        session = async_get_clientsession(self.hass)
        discovery_url = str(self.entry.data[CONF_GBFS_URL])
        try:
            latitude = required_float(self.entry.options[CONF_LATITUDE])
            longitude = required_float(self.entry.options[CONF_LONGITUDE])
            radius_meters = required_int(
                self.entry.options.get(CONF_RADIUS, DEFAULT_RADIUS_METERS)
            )
            min_battery_percent = required_int(
                self.entry.options.get(CONF_MIN_BATTERY, DEFAULT_MIN_BATTERY_PERCENT)
            )
            discovery = await async_fetch_json(session, discovery_url)
            self.update_interval = timedelta(
                seconds=scan_interval_seconds(parse_ttl_seconds(discovery))
            )
            feeds = parse_discovery_feeds(discovery, discovery_url)
            if not feeds_are_usable(feeds):
                raise GbfsNoVehicleFeedError(
                    "GBFS discovery has no vehicle or station feeds"
                )
            vehicle_url = feeds.get(FEED_VEHICLE_STATUS) or feeds.get(
                FEED_FREE_BIKE_STATUS
            )
            info_url, status_url = _station_urls(feeds)
            (
                system_payload,
                vehicle_payload,
                station_info,
                station_status,
            ) = await asyncio.gather(
                _fetch_system_information(session, feeds.get(FEED_SYSTEM_INFORMATION)),
                _fetch_required(session, vehicle_url),
                _fetch_required(session, info_url),
                _fetch_required(session, status_url),
            )
            vehicles = _vehicles_in_radius(
                vehicle_payload,
                latitude=latitude,
                longitude=longitude,
                radius_meters=radius_meters,
                min_battery_percent=min_battery_percent,
            )
            stations = _stations_in_radius(
                station_info,
                station_status,
                latitude=latitude,
                longitude=longitude,
                radius_meters=radius_meters,
            )
        except GbfsError as err:
            raise UpdateFailed(str(err)) from err

        operator_name, android_store_uri, ios_store_uri = parse_system_details(
            system_payload
        )
        return GbfsData(
            operator_name=operator_name,
            android_store_uri=android_store_uri,
            ios_store_uri=ios_store_uri,
            vehicles=vehicles,
            stations=stations,
            radius_meters=radius_meters,
            min_battery_percent=min_battery_percent,
            last_updated=parse_last_updated(vehicle_payload, station_status, discovery),
        )


GbfsConfigEntry: TypeAlias = ConfigEntry[GbfsCoordinator]


def configured_max_vehicles(entry: ConfigEntry) -> int:
    """Return the configured device-tracker cap."""
    return required_int(entry.options.get(CONF_MAX_VEHICLES, DEFAULT_MAX_VEHICLES))


def required_float(value: object) -> float:
    """Return a JSON number as a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GbfsInvalidFeedError("expected a number")
    return float(value)


def required_int(value: object) -> int:
    """Return a JSON number as an int."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GbfsInvalidFeedError("expected a number")
    return int(value)


def feeds_are_usable(feeds: Mapping[str, str]) -> bool:
    """Return whether discovery can produce a vehicle count."""
    has_vehicles = FEED_VEHICLE_STATUS in feeds or FEED_FREE_BIKE_STATUS in feeds
    has_stations = FEED_STATION_INFORMATION in feeds and FEED_STATION_STATUS in feeds
    return has_vehicles or has_stations


def validate_gbfs_url(url: str) -> str:
    """Return a stripped http(s) discovery URL."""
    stripped = url.strip()
    parts = urlsplit(stripped)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise GbfsInvalidFeedError("GBFS URL must be http or https")
    return stripped


def normalize_gbfs_url(url: str) -> str:
    """Return a stable id for a discovery URL."""
    parts = urlsplit(validate_gbfs_url(url))
    path = parts.path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


async def async_validate_discovery(session: ClientSession, url: str) -> dict[str, str]:
    """Fetch gbfs.json and require a vehicle feed or a station pair."""
    checked = validate_gbfs_url(url)
    payload = await async_fetch_json(session, checked)
    feeds = parse_discovery_feeds(payload, checked)
    if not feeds_are_usable(feeds):
        raise GbfsNoVehicleFeedError("GBFS discovery has no vehicle or station feeds")
    return feeds


async def async_fetch_json(session: ClientSession, url: str) -> object:
    """GET a URL and decode the body as JSON."""
    timeout = ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    try:
        async with session.get(url, timeout=timeout) as response:
            if response.status >= 400:
                raise GbfsConnectionError(f"HTTP {response.status} from {url}")
            try:
                return await response.json(content_type=None)
            except json.JSONDecodeError as err:
                raise GbfsInvalidFeedError(f"Invalid JSON from {url}") from err
    except GbfsError:
        raise
    except (ClientError, TimeoutError) as err:
        raise GbfsConnectionError(str(err)) from err


def parse_discovery_feeds(payload: object, discovery_url: str) -> dict[str, str]:
    """Map feed names to absolute URLs from a v2 or v3 discovery document."""
    data = _data_object(payload)
    feeds = _feed_list(data)
    resolved: dict[str, str] = {}
    for item in feeds:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        url = item.get("url")
        if not isinstance(name, str) or not name or not isinstance(url, str) or not url:
            continue
        resolved[name] = _absolute_http_url(discovery_url, url)
    if not resolved:
        raise GbfsInvalidFeedError("GBFS discovery file has no feeds")
    return resolved


def parse_ttl_seconds(payload: object) -> int | None:
    """Return the discovery document TTL, or None when it is missing."""
    if not isinstance(payload, dict):
        return None
    ttl = payload.get("ttl")
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl < 0:
        return None
    return int(ttl)


def scan_interval_seconds(ttl: int | None) -> int:
    """Return how often to poll, never faster than the minimum interval."""
    if ttl is None:
        return DEFAULT_SCAN_INTERVAL_SECONDS
    return max(ttl, MIN_SCAN_INTERVAL_SECONDS)


def parse_system_details(
    payload: object | None,
) -> tuple[str | None, str | None, str | None]:
    """Return the operator name and the Android and iOS store URLs."""
    if payload is None:
        return None, None, None
    try:
        data = _data_object(payload)
    except GbfsInvalidFeedError:
        return None, None, None
    name = _localized_text(data.get("name")) or _localized_text(data.get("operator"))
    apps = data.get("rental_apps")
    if not isinstance(apps, dict):
        return name, None, None
    return name, _store_uri(apps.get("android")), _store_uri(apps.get("ios"))


def parse_vehicles(
    payload: object,
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
    min_battery_percent: int = 0,
) -> tuple[GbfsVehicle, ...]:
    """Return available vehicles inside the radius, closest first."""
    data = _data_object(payload)
    rows = data.get("vehicles")
    if rows is None:
        rows = data.get("bikes")
    if not isinstance(rows, list):
        raise GbfsInvalidFeedError("Vehicle feed is missing a vehicle list")

    closest_by_id: dict[str, GbfsVehicle] = {}
    for row in rows:
        vehicle = _parse_vehicle(
            row,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius_meters,
            min_battery_percent=min_battery_percent,
        )
        if vehicle is None:
            continue
        current = closest_by_id.get(vehicle.vehicle_id)
        if current is None or vehicle.distance_m < current.distance_m:
            closest_by_id[vehicle.vehicle_id] = vehicle
    return tuple(
        sorted(
            closest_by_id.values(),
            key=lambda vehicle: (vehicle.distance_m, vehicle.vehicle_id),
        )
    )


def parse_stations(
    information: object,
    status: object,
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
) -> tuple[GbfsStation, ...]:
    """Return renting stations inside the radius, closest first."""
    names, locations = _station_details(_data_object(information))
    statuses = _data_object(status).get("stations")
    if not isinstance(statuses, list):
        raise GbfsInvalidFeedError("Station status feed is missing stations")

    stations: list[GbfsStation] = []
    for row in statuses:
        if not isinstance(row, dict):
            continue
        station_id = _as_id(row.get("station_id"))
        if station_id is None:
            continue
        coords = locations.get(station_id)
        if coords is None or not _station_is_renting(row):
            continue
        station_distance_m = distance_m(latitude, longitude, coords[0], coords[1])
        if station_distance_m is None or station_distance_m > radius_meters:
            continue
        available = row.get("num_vehicles_available")
        if available is None:
            available = row.get("num_bikes_available")
        count = _optional_int(available)
        stations.append(
            GbfsStation(
                station_id=station_id,
                name=names.get(station_id),
                latitude=coords[0],
                longitude=coords[1],
                distance_m=station_distance_m,
                vehicles_available=0 if count is None else count,
                docks_available=_optional_int(row.get("num_docks_available")),
            )
        )
    stations.sort(key=lambda station: (station.distance_m, station.station_id))
    return tuple(stations)


def parse_last_updated(*payloads: object | None) -> datetime:
    """Return the newest feed timestamp, or now when none are present."""
    latest: datetime | None = None
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        parsed = _parse_timestamp(payload.get("last_updated"))
        if parsed is None:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest if latest is not None else utcnow()


async def _fetch_required(session: ClientSession, url: str | None) -> object | None:
    if url is None:
        return None
    return await async_fetch_json(session, url)


async def _fetch_system_information(
    session: ClientSession, url: str | None
) -> object | None:
    if url is None:
        return None
    try:
        return await async_fetch_json(session, url)
    except GbfsError as err:
        _LOGGER.debug("Skipping system_information: %s", err)
        return None


def _station_urls(feeds: Mapping[str, str]) -> tuple[str | None, str | None]:
    info_url = feeds.get(FEED_STATION_INFORMATION)
    status_url = feeds.get(FEED_STATION_STATUS)
    if info_url is None or status_url is None:
        return None, None
    return info_url, status_url


def _vehicles_in_radius(
    payload: object | None,
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
    min_battery_percent: int,
) -> tuple[GbfsVehicle, ...]:
    if payload is None:
        return ()
    return parse_vehicles(
        payload,
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
        min_battery_percent=min_battery_percent,
    )


def _stations_in_radius(
    information: object | None,
    status: object | None,
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
) -> tuple[GbfsStation, ...]:
    if information is None or status is None:
        return ()
    return parse_stations(
        information,
        status,
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
    )


def _feed_list(data: Mapping[str, object]) -> list[object]:
    feeds = data.get("feeds")
    if isinstance(feeds, list):
        return feeds

    english = data.get("en")
    if isinstance(english, dict):
        nested = english.get("feeds")
        if isinstance(nested, list):
            return nested

    for value in data.values():
        if not isinstance(value, dict):
            continue
        nested = value.get("feeds")
        if isinstance(nested, list):
            return nested
    raise GbfsInvalidFeedError("GBFS discovery file has no feeds")


def _absolute_http_url(discovery_url: str, feed_url: str) -> str:
    absolute = urljoin(discovery_url, feed_url)
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise GbfsInvalidFeedError("Feed URL must be http or https")
    return absolute


def _data_object(payload: object) -> dict[str, object]:
    root = _as_dict(payload)
    return _as_dict(root.get("data"))


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GbfsInvalidFeedError("expected a JSON object")
    return {str(key): item for key, item in value.items()}


def _parse_vehicle(
    row: object,
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
    min_battery_percent: int,
) -> GbfsVehicle | None:
    if not isinstance(row, dict):
        return None
    reserved = _flag(row, "is_reserved", default=False)
    disabled = _flag(row, "is_disabled", default=False)
    if reserved or disabled:
        return None
    vehicle_id = _as_id(row.get("vehicle_id")) or _as_id(row.get("bike_id"))
    if vehicle_id is None:
        return None
    lat = _optional_float(row.get("lat"))
    lon = _optional_float(row.get("lon"))
    if lat is None or lon is None:
        return None
    vehicle_distance_m = distance_m(latitude, longitude, lat, lon)
    if vehicle_distance_m is None:
        return None
    if vehicle_distance_m > radius_meters:
        return None
    battery_level = _battery_level(row.get("current_fuel_percent"))
    if min_battery_percent > 0 and (
        battery_level is None or battery_level < min_battery_percent
    ):
        return None
    return GbfsVehicle(
        vehicle_id=vehicle_id,
        latitude=lat,
        longitude=lon,
        distance_m=vehicle_distance_m,
        battery_level=battery_level,
        current_range_meters=_optional_float(row.get("current_range_meters")),
        rental_uri=_rental_uri(row),
    )


def _store_uri(platform: object) -> str | None:
    if not isinstance(platform, dict):
        return None
    return _http_url(platform.get("store_uri"))


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    parts = urlsplit(candidate)
    if parts.scheme in {"http", "https"} and parts.netloc:
        return candidate
    return None


def _rental_uri(row: Mapping[str, object]) -> str | None:
    raw = row.get("rental_uris")
    if not isinstance(raw, dict):
        return None
    for key in ("web", "android", "ios"):
        candidate = _http_url(raw.get(key))
        if candidate is not None:
            return candidate
    return None


def _station_details(
    data: Mapping[str, object],
) -> tuple[dict[str, str | None], dict[str, tuple[float, float]]]:
    rows = data.get("stations")
    if not isinstance(rows, list):
        raise GbfsInvalidFeedError("Station information feed is missing stations")
    names: dict[str, str | None] = {}
    locations: dict[str, tuple[float, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        station_id = _as_id(row.get("station_id"))
        lat = _optional_float(row.get("lat"))
        lon = _optional_float(row.get("lon"))
        if station_id is None or lat is None or lon is None:
            continue
        names[station_id] = _localized_text(row.get("name"))
        locations[station_id] = (lat, lon)
    return names, locations


def _station_is_renting(row: Mapping[str, object]) -> bool:
    return _flag(row, "is_installed", default=True) and _flag(
        row, "is_renting", default=True
    )


def _flag(row: Mapping[str, object], key: str, *, default: bool) -> bool:
    if key not in row:
        return default
    return _as_bool(row[key])


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


def _as_id(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _battery_level(value: object) -> int | None:
    percent = _optional_float(value)
    if percent is None or percent < 0:
        return None
    if percent <= 1:
        percent *= 100
    return max(0, min(100, round(percent)))


def _localized_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if not isinstance(value, list):
        return None
    fallback: str | None = None
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        stripped = text.strip()
        language = item.get("language")
        if isinstance(language, str) and language.lower().startswith("en"):
            return stripped
        if fallback is None:
            fallback = stripped
    return fallback


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
