"""Credential-free discovery diagnostics, without probing or network access."""


async def async_get_config_entry_diagnostics(hass, entry):
    """Report discovery evidence without addresses, IDs or credentials."""
    method = entry.data.get("discovery")
    return {
        "discovery": "zeroconf" if method == "zeroconf" else "manual_or_dhcp",
        "zeroconf_discovery_seen": bool(entry.discovery_keys.get("zeroconf")),
    }
