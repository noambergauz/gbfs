"""Constants for the GBFS integration."""

from typing import Final

DOMAIN: Final = "gbfs"

DEFAULT_RADIUS_METERS: Final = 500
DEFAULT_MAX_VEHICLES: Final = 5
DEFAULT_MIN_BATTERY_PERCENT: Final = 0
DEFAULT_SCAN_INTERVAL_SECONDS: Final = 120
MIN_SCAN_INTERVAL_SECONDS: Final = 60
REQUEST_TIMEOUT_SECONDS: Final = 15

CONF_GBFS_URL: Final = "gbfs_url"
CONF_RADIUS: Final = "radius_meters"
CONF_MAX_VEHICLES: Final = "max_vehicles"
CONF_MIN_BATTERY: Final = "min_battery_percent"

FEED_SYSTEM_INFORMATION: Final = "system_information"
FEED_FREE_BIKE_STATUS: Final = "free_bike_status"
FEED_VEHICLE_STATUS: Final = "vehicle_status"
FEED_STATION_INFORMATION: Final = "station_information"
FEED_STATION_STATUS: Final = "station_status"

ATTR_CLOSEST_VEHICLE_DISTANCE_M: Final = "closest_vehicle_distance_m"
ATTR_SEARCH_RADIUS_M: Final = "search_radius_m"
ATTR_OPERATOR_NAME: Final = "operator_name"
ATTR_LAST_UPDATED: Final = "last_updated"
ATTR_VEHICLE_ID: Final = "vehicle_id"
ATTR_BATTERY_LEVEL: Final = "battery_level"
ATTR_CURRENT_RANGE_METERS: Final = "current_range_meters"
ATTR_DISTANCE_M: Final = "distance_m"
ATTR_LAT: Final = "lat"
ATTR_LON: Final = "lon"
ATTR_RENTAL_URI: Final = "rental_uri"
ATTR_STATION_ID: Final = "station_id"
ATTR_STATION_NAME: Final = "station_name"
ATTR_VEHICLES_AVAILABLE: Final = "vehicles_available"
ATTR_DOCKS_AVAILABLE: Final = "docks_available"
ATTR_ANDROID_STORE_URI: Final = "android_store_uri"
ATTR_IOS_STORE_URI: Final = "ios_store_uri"
