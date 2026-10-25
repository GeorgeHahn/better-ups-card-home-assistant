import asyncio
import copy
import random
from types import MappingProxyType
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from hypothesis import given
from hypothesis import strategies as st

from custom_components.better_ups_card import DOMAIN, async_setup_entry
from custom_components.better_ups_card.api import Card, CardError, admission, broker_fields, encode
from custom_components.better_ups_card.config_flow import BetterUpsFlow

# Deliberately synthetic credentials, used only by the in-process mock card.
TEST_WEB_PASSWORD = "web-secret"  # pragma: allowlist secret
TEST_BROKER_PASSWORD = "broker-secret"  # pragma: allowlist secret
DEVICE = "ups020000000001"
CONFIG = {
    "device_id": DEVICE,
    "revision": 7,
    "nut_available": True,
    "protocols": {"snmp_v1": True, "snmp_v2c": True, "snmp_v3": True, "nut_enabled": True},
    "memory": {"capacity": 30752, "profiles": [2016, 7224, 9120, 14328, 4640, 9848, 11744, 16952]},
}


def entry(domain, data, unique_id=None):
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=domain,
        title=domain,
        data=data,
        options={},
        source="user",
        unique_id=unique_id,
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )


@pytest_asyncio.fixture
async def hass(tmp_path):
    h = HomeAssistant(str(tmp_path))
    h.config_entries = ConfigEntries(h, {})
    await asyncio.wait_for(h.config_entries.async_initialize(), 5)
    dr.async_setup(h)
    await asyncio.wait_for(dr.async_load(h), 5)
    e = entry(
        "mqtt",
        {
            "broker": "192.0.2.9",
            "port": 1883,
            "username": "ha",
            "password": TEST_BROKER_PASSWORD,
        },
    )
    h.config_entries._entries[e.entry_id] = e
    yield h
    await h.async_stop()


class Device:
    """Independent HTTP peer with optimistic concurrency and atomic rejection."""

    def __init__(self):
        self.config = copy.deepcopy(CONFIG)
        self.writes = []
        self.failure = None
        self.auth = False

    def __call__(self, request):
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
        assert request.url.path == "/api/config"
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


def flow(hass, device):
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


@pytest.mark.asyncio
async def test_dhcp_onboarding_uses_digest_and_native_mqtt_device(hass):
    device = Device()
    device.auth = True
    f, factory = flow(hass, device)
    with factory:
        result = await f.async_step_dhcp(
            DhcpServiceInfo(ip="192.0.2.1", hostname="better-ups-card", macaddress="020000000001")
        )
        assert result["step_id"] == "user"
        assert not device.writes
        result = await f.async_step_user(
            {
                "host": "192.0.2.1",
                "username": "admin",
                "password": TEST_WEB_PASSWORD,
            }
        )
        assert result["step_id"] == "broker"
        assert TEST_BROKER_PASSWORD not in str(result)
        result = await f.async_step_broker(broker_input(confirm_plaintext=False))
        assert result["errors"] == {"base": "confirm_required"} and not device.writes
        result = await f.async_step_broker(broker_input())
    assert result["type"] == "create_entry"
    assert result["data"] == {"host": "192.0.2.1"}
    assert f._card is None and not f._broker
    assert device.writes[0]["mqtt_password"] == TEST_BROKER_PASSWORD
    assert device.writes[0]["mqtt_enabled"] == "1"
    # Native MQTT creates the same identifier; onboarding must not split it.
    mqtt = hass.config_entries.async_entries("mqtt")[0]
    registry = dr.async_get(hass)
    existing = registry.async_get_or_create(
        config_entry_id=mqtt.entry_id, identifiers={("mqtt", DEVICE)}
    )
    onboard = entry(DOMAIN, result["data"], DEVICE)
    hass.config_entries._entries[onboard.entry_id] = onboard
    assert await async_setup_entry(hass, onboard)
    assert len(registry.devices) == 1
    assert registry.async_get(existing.id).config_entry_id == mqtt.entry_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [
        (401, "invalid_auth"),
        (409, "config_changed"),
        (422, "resources"),
        (500, "save_uncertain"),
        ("disconnect", "save_uncertain"),
    ],
)
async def test_rejected_and_uncertain_save_never_retries_or_creates_entry(hass, failure, expected):
    device = Device()
    f, factory = flow(hass, device)
    with factory:
        await f.async_step_user(
            {
                "host": "192.0.2.1",
                "username": "admin",
                "password": TEST_WEB_PASSWORD,
            }
        )
        device.failure = failure
        result = await f.async_step_broker(broker_input())
    assert result["errors"] == {"base": expected} and not device.writes


@pytest.mark.asyncio
async def test_detected_identity_mismatch_and_exhaustion_prevent_writes(hass):
    for changed, expected in [("device_id", "wrong_device"), ("capacity", "resources")]:
        device = Device()
        f, factory = flow(hass, device)
        await f.async_step_dhcp(
            DhcpServiceInfo(ip="192.0.2.1", hostname="better-ups-card", macaddress="020000000001")
        )
        if changed == "device_id":
            device.config["device_id"] = "ups020000000002"
        else:
            device.config["memory"]["capacity"] = 16951
        with factory:
            result = await f.async_step_user(
                {
                    "host": "192.0.2.1",
                    "username": "admin",
                    "password": TEST_WEB_PASSWORD,
                }
            )
        assert result["errors"] == {"base": expected} and not device.writes


@pytest.mark.asyncio
async def test_duplicate_discovery_updates_ip_without_reprovisioning(hass):
    e = entry(DOMAIN, {"host": "192.0.2.1"}, DEVICE)
    hass.config_entries._entries[e.entry_id] = e
    f, _ = flow(hass, Device())
    with pytest.raises(AbortFlow, match="already_configured"):
        await f.async_step_dhcp(
            DhcpServiceInfo(ip="192.0.2.2", hostname="better-ups-card", macaddress="020000000001")
        )
    assert e.data["host"] == "192.0.2.2"


@pytest.mark.asyncio
async def test_deterministic_resource_and_network_schedule():
    rng = random.Random(20261101)
    device = Device()
    card = Card("192.0.2.1", "admin", "secret", transport=httpx.MockTransport(device))
    values = broker_fields("192.0.2.9", 1883, "ha", "secret")
    expected = 0
    for _ in range(256):
        device.failure = rng.choice([None, 409, 422, "disconnect"])
        device.config["memory"]["capacity"] = rng.choice([16951, 16952, 30752])
        succeeds = device.failure is None and device.config["memory"]["capacity"] >= 16952
        try:
            await card.provision(DEVICE, values)
        except CardError:
            assert not succeeds
        else:
            assert succeeds
            expected += 1
        assert len(device.writes) == expected
        assert device.config["revision"] == 7 + expected


@given(
    st.dictionaries(
        st.sampled_from(["mqtt_user", "mqtt_password", "mqtt_address"]),
        st.text(max_size=64),
        max_size=3,
    )
)
def test_hex_fields_roundtrip_without_line_injection(values):
    encoded = encode(values)
    decoded = {
        key: bytes.fromhex(value).decode()
        for key, value in (line.split("=", 1) for line in encoded.decode().splitlines())
    }
    assert decoded == values


@given(st.booleans(), st.booleans(), st.booleans(), st.integers(min_value=-1, max_value=1))
def test_exact_target_profile_admission(snmp, nut, enabled, spare):
    config = copy.deepcopy(CONFIG)
    config["protocols"].update(snmp_v1=snmp, snmp_v2c=False, snmp_v3=False, nut_enabled=nut)
    config["nut_available"] = enabled
    config["memory"]["capacity"] = (
        config["memory"]["profiles"][int(snmp) + 2 * int(nut and enabled) + 4] + spare
    )
    if spare < 0:
        with pytest.raises(CardError, match="resources"):
            admission(config)
    else:
        admission(config)


@given(
    st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40),
    st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40),
)
def test_broker_bounds_never_truncate_credentials(user, password):
    valid = len(user) <= 24 and len(password) <= 32 and (bool(user) or not password)
    try:
        fields = broker_fields("192.0.2.9", 1883, user, password)
    except CardError:
        assert not valid
    else:
        assert valid and fields["mqtt_user"] == user and fields.get("mqtt_password", "") == password


@pytest.mark.parametrize(
    "override",
    [
        {"transport": "websockets"},
        {"certificate": "auto"},
        {"client_cert": "cert"},
        {"tls_insecure": True},
        {"port": 8883},
        {"discovery_prefix": "custom"},
        {"discovery": False},
    ],
)
async def test_incompatible_broker_never_provisions(hass, override):
    mqtt = hass.config_entries.async_entries("mqtt")[0]
    hass.config_entries.async_update_entry(mqtt, data={**mqtt.data, **override})
    device = Device()
    f, factory = flow(hass, device)
    with factory:
        result = await f.async_step_user(
            {
                "host": "192.0.2.1",
                "username": "admin",
                "password": TEST_WEB_PASSWORD,
            }
        )
    assert result["errors"] == {"base": "mqtt_incompatible"}
    assert not device.writes


async def test_missing_broker_and_dns_failure(hass):
    device = Device()
    f, factory = flow(hass, device)
    with factory:
        mqtt = hass.config_entries.async_entries("mqtt")[0]
        hass.config_entries.async_update_entry(mqtt, data={"broker": "broker.invalid"})
        with patch("socket.getaddrinfo", side_effect=OSError):
            result = await f.async_step_user(
                {
                    "host": "192.0.2.1",
                    "username": "admin",
                    "password": TEST_WEB_PASSWORD,
                }
            )
        assert result["errors"] == {"base": "cannot_connect"}
        hass.config_entries._entries.pop(mqtt.entry_id)
        result = await f.async_step_user(
            {
                "host": "192.0.2.1",
                "username": "admin",
                "password": TEST_WEB_PASSWORD,
            }
        )
    assert result["errors"] == {"base": "mqtt_required"}
    assert not device.writes


@pytest.mark.parametrize("broker", ["core-mosquitto", "localhost", "127.0.0.1", "broker.invalid"])
async def test_broker_resolution_and_manual_anonymous_account(hass, broker):
    mqtt = hass.config_entries.async_entries("mqtt")[0]
    hass.config_entries.async_update_entry(mqtt, data={**mqtt.data, "broker": broker})
    device = Device()
    f, factory = flow(hass, device)
    with (
        factory,
        patch(
            "custom_components.better_ups_card.config_flow.network.async_get_source_ip",
            return_value="192.0.2.9",
        ),
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.0.2.9", 0))]),
    ):
        result = await f.async_step_user(
            {
                "host": "192.0.2.1",
                "username": "admin",
                "password": TEST_WEB_PASSWORD,
            }
        )
        assert result["step_id"] == "broker"
        assert f._broker["address"] == "192.0.2.9"
        result = await f.async_step_broker(
            broker_input(use_existing_credentials=False, username="", password="")
        )
    assert result["type"] == "create_entry"
    assert device.writes[0]["mqtt_clear_password"] == "1"
    assert "mqtt_password" not in device.writes[0]


async def test_invalid_broker_returns_form_and_changed_device_prevents_save(hass):
    device = Device()
    f, factory = flow(hass, device)
    with factory:
        await f.async_step_user(
            {"host": "192.0.2.1", "username": "admin", "password": TEST_WEB_PASSWORD}
        )
        result = await f.async_step_broker(broker_input(address="127.0.0.1"))
        assert result["errors"] == {"base": "broker_config"}
        device.config["device_id"] = "ups020000000002"
        result = await f.async_step_broker(broker_input())
    assert result["errors"] == {"base": "wrong_device"}
    assert not device.writes


async def test_real_loader_and_flow_manager(hass):
    from homeassistant import loader

    from custom_components.better_ups_card import async_unload_entry

    loader.async_setup(hass)
    integration = await loader.async_get_integration(hass, DOMAIN)
    assert integration.version == "0.1.0"
    assert integration.manifest["dhcp"] == [{"hostname": "better-ups-card"}]
    # Avoid starting a real MQTT connection; exercise HA's actual flow manager.
    with patch("homeassistant.config_entries.async_process_deps_reqs", return_value=None):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "user"
    assert not result["errors"]
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert await async_unload_entry(hass, entry(DOMAIN, {"host": "192.0.2.1"}, DEVICE))
