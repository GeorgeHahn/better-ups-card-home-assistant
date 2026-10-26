"""Synthetic card responses and factories shared by the integration tests."""

from types import MappingProxyType
from unittest.mock import patch

import httpx
from homeassistant.config_entries import ConfigEntry

from custom_components.better_ups_card import DOMAIN
from custom_components.better_ups_card.api import Card
from custom_components.better_ups_card.config_flow import BetterUpsFlow

# Deliberately synthetic credentials, used only by the in-process mock card.
TEST_WEB_PASSWORD = "web-secret"
TEST_BROKER_PASSWORD = "broker-secret"
DEVICE = "ups020000000001"


def card_config() -> dict:
    """Return an independent firmware response for each test or mock card."""
    return {
        "device_id": DEVICE,
        "revision": 7,
        "nut_available": True,
        "protocols": {"snmp_v1": True, "snmp_v2c": True, "snmp_v3": True, "nut_enabled": True},
        "memory": {
            "capacity": 30752,
            "profiles": [2016, 7224, 9120, 14328, 4640, 9848, 11744, 16952],
        },
    }


def make_entry(domain, data, unique_id=None, discovery_keys=None):
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=domain,
        title=domain,
        data=data,
        options={},
        source="user",
        unique_id=unique_id,
        discovery_keys=MappingProxyType(discovery_keys or {}),
        subentries_data=[],
    )


class MockCard:
    """Independent HTTP peer with optimistic concurrency and atomic rejection."""

    def __init__(self):
        self.config = card_config()
        self.writes = []
        self.failure = None
        self.auth = False

    def __call__(self, request):
        assert request.url.path == "/api/config"
        assert request.method in ("GET", "POST")
        if self.auth and "authorization" not in request.headers:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Digest realm="better-ups-card", nonce="nonce", algorithm=SHA-256, qop="auth"'
                },
            )
        if self.auth:
            assert request.headers["authorization"].startswith("Digest ")
            assert "algorithm=SHA-256" in request.headers["authorization"]
            assert TEST_WEB_PASSWORD not in request.headers["authorization"]
        if request.method == "GET":
            return httpx.Response(200, json=self.config)
        assert request.headers["x-requested-with"] == "better-ups-card"
        values = dict(line.split("=", 1) for line in request.content.decode().splitlines())
        values = {k: bytes.fromhex(v).decode() for k, v in values.items()}
        assert set(values).issubset(
            {
                "revision",
                "mqtt_enabled",
                "mqtt_discovery",
                "mqtt_address",
                "mqtt_port",
                "mqtt_user",
                "mqtt_password",
                "mqtt_clear_password",
            }
        )
        if self.failure == "disconnect":
            raise httpx.ReadError("disconnected")
        if self.failure:
            return httpx.Response(self.failure)
        assert values["revision"] == str(self.config["revision"])
        self.writes.append(values)
        self.config["revision"] += 1
        return httpx.Response(202, text="Settings saved")


def make_flow(hass, device):
    f = BetterUpsFlow()
    f.hass = hass
    f.handler = DOMAIN
    f.flow_id = "test-flow"
    f.context = {"source": "user"}

    def factory(host, user, password):
        return Card(host, user, password, transport=httpx.MockTransport(device))

    return f, patch("custom_components.better_ups_card.config_flow.Card", factory)


def broker_input(**kwargs):
    return {
        "address": "192.0.2.9",
        "port": 1883,
        "use_existing_credentials": True,
        "confirm_plaintext": True,
        **kwargs,
    }


def zeroconf_info(**changes):
    from ipaddress import IPv4Address

    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

    fields = {
        "ip_address": IPv4Address("192.0.2.1"),
        "ip_addresses": [IPv4Address("192.0.2.1")],
        "port": 80,
        "hostname": DEVICE + ".local.",
        "type": "_http._tcp.local.",
        "name": DEVICE + "._http._tcp.local.",
        "properties": {"product": "better-ups-card", "id": DEVICE, "path": "/"},
    }
    return ZeroconfServiceInfo(**{**fields, **changes})
