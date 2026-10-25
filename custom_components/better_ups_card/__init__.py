"""Onboarding only: Home Assistant's native MQTT integration owns the device."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

DOMAIN = "better_ups_card"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Keep a discovery record, without creating a duplicate device or entities.

    Current HA device identities are scoped to config entries. Registering the
    MQTT identifier here would produce a second device, not share MQTT's device.
    The native MQTT device already supplies its authenticated configuration URL.
    """
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Removing onboarding never changes UPS or MQTT settings."""
    return True
