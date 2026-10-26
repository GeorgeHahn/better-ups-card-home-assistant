"""Discovery and onboarding through mocked card HTTP requests."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from custom_components.better_ups_card import DOMAIN, async_setup_entry

from .helpers import (
    DEVICE,
    TEST_BROKER_PASSWORD,
    TEST_WEB_PASSWORD,
    MockCard,
    broker_input,
    make_entry,
    make_flow,
    zeroconf_info,
)


async def test_dhcp_onboarding_uses_digest_and_native_mqtt_device(hass):
    device = MockCard()
    device.auth = True
    f, factory = make_flow(hass, device)
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
    onboard = make_entry(DOMAIN, result["data"], DEVICE)
    hass.config_entries._entries[onboard.entry_id] = onboard
    assert await async_setup_entry(hass, onboard)
    assert len(registry.devices) == 1
    assert registry.async_get(existing.id).config_entry_id == mqtt.entry_id


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
    device = MockCard()
    f, factory = make_flow(hass, device)
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


async def test_detected_identity_mismatch_and_exhaustion_prevent_writes(hass):
    for changed, expected in [("device_id", "wrong_device"), ("capacity", "resources")]:
        device = MockCard()
        f, factory = make_flow(hass, device)
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


async def test_duplicate_discovery_updates_ip_without_reprovisioning(hass):
    e = make_entry(DOMAIN, {"host": "192.0.2.1"}, DEVICE)
    hass.config_entries._entries[e.entry_id] = e
    f, _ = make_flow(hass, MockCard())
    with pytest.raises(AbortFlow, match="already_configured"):
        await f.async_step_dhcp(
            DhcpServiceInfo(ip="192.0.2.2", hostname="better-ups-card", macaddress="020000000001")
        )
    assert e.data["host"] == "192.0.2.2"


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
    device = MockCard()
    f, factory = make_flow(hass, device)
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
    device = MockCard()
    f, factory = make_flow(hass, device)
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
    device = MockCard()
    f, factory = make_flow(hass, device)
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
    device = MockCard()
    f, factory = make_flow(hass, device)
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
    manifest_path = Path(__file__).parents[1] / "custom_components" / DOMAIN / "manifest.json"
    assert integration.version == json.loads(manifest_path.read_text())["version"]
    assert integration.manifest["dhcp"] == [{"hostname": "better-ups-card"}]
    # Avoid starting a real MQTT connection; exercise HA's actual flow manager.
    with patch("homeassistant.config_entries.async_process_deps_reqs", return_value=None):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "user"
    assert not result["errors"]
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert await async_unload_entry(hass, make_entry(DOMAIN, {"host": "192.0.2.1"}, DEVICE))


async def test_manual_setup_can_complete_while_dhcp_discovery_is_pending(hass):
    from homeassistant import loader

    loader.async_setup(hass)
    device = MockCard()
    _, factory = make_flow(hass, device)
    manager = hass.config_entries.flow
    with (
        factory,
        patch("homeassistant.config_entries.async_process_deps_reqs", return_value=None),
        patch.object(ConfigEntry, "async_setup", return_value=True),
    ):
        discovered = await manager.async_init(
            DOMAIN,
            context={"source": "dhcp"},
            data=DhcpServiceInfo(
                ip="192.0.2.1", hostname="better-ups-card", macaddress="020000000001"
            ),
        )
        assert discovered["step_id"] == "user" and not device.writes
        manual = await manager.async_init(DOMAIN, context={"source": "user"})
        result = await manager.async_configure(
            manual["flow_id"],
            {"host": "192.0.2.1", "username": "admin", "password": TEST_WEB_PASSWORD},
        )
        assert result["step_id"] == "broker"
        result = await manager.async_configure(manual["flow_id"], broker_input())
        assert result["type"] == "create_entry"
        assert len(hass.config_entries.async_entries(DOMAIN)) == 1
        assert len(device.writes) == 1
        assert not any(progress["handler"] == DOMAIN for progress in manager.async_progress())


async def test_new_firmware_advertises_expanded_credentials(hass):
    mqtt = hass.config_entries.async_entries("mqtt")[0]
    hass.config_entries.async_update_entry(
        mqtt, data={**mqtt.data, "username": "u" * 64, "password": "p" * 128}
    )
    device = MockCard()
    device.config["mqtt_limits"] = {"username": 64, "password": 128}
    f, factory = make_flow(hass, device)
    with factory:
        result = await f.async_step_user(
            {"host": "192.0.2.1", "username": "admin", "password": TEST_WEB_PASSWORD}
        )
        assert result["description_placeholders"]["password_max"] == "128"
        result = await f.async_step_broker(broker_input())
    assert result["type"] == "create_entry"
    assert device.writes[0]["mqtt_user"] == "u" * 64
    assert device.writes[0]["mqtt_password"] == "p" * 128


async def test_zeroconf_onboarding_uses_stable_txt_id_despite_conflict_rename(hass):
    device = MockCard()
    f, factory = make_flow(hass, device)
    with factory:
        result = await f.async_step_zeroconf(
            zeroconf_info(hostname=DEVICE + "-2.local.", name=DEVICE + "-2._http._tcp.local.")
        )
        assert result["step_id"] == "user" and not device.writes
        assert f.unique_id == DEVICE
        result = await f.async_step_user(
            {"host": "192.0.2.1", "username": "admin", "password": TEST_WEB_PASSWORD}
        )
        assert result["step_id"] == "broker"
        result = await f.async_step_broker(broker_input())
    assert result["data"] == {"host": "192.0.2.1", "discovery": "zeroconf"}
    assert len(device.writes) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "_other._tcp.local."},
        {"port": 8080},
        {"properties": {"id": DEVICE}},
        {"properties": {"product": "different", "id": DEVICE}},
        {"properties": {"product": "better-ups-card", "id": 42}},
        {"properties": {"product": "better-ups-card", "id": "invalid"}},
    ],
)
async def test_zeroconf_rejects_unrelated_or_malformed_services(hass, changes):
    device = MockCard()
    f, factory = make_flow(hass, device)
    with factory:
        result = await f.async_step_zeroconf(zeroconf_info(**changes))
    assert result["reason"] == "invalid_discovery" and not device.writes


async def test_zeroconf_rejects_ipv6_only_address(hass):
    from ipaddress import IPv6Address

    f, factory = make_flow(hass, MockCard())
    with factory:
        result = await f.async_step_zeroconf(zeroconf_info(ip_address=IPv6Address("2001:db8::1")))
    assert result["reason"] == "invalid_discovery"


async def test_zeroconf_updates_existing_address_and_dhcp_cannot_override(hass):
    from ipaddress import IPv4Address

    existing = make_entry(DOMAIN, {"host": "192.0.2.2"}, DEVICE)
    hass.config_entries._entries[existing.entry_id] = existing
    device = MockCard()
    for address in ["192.0.2.3", "192.0.2.4"]:
        f, factory = make_flow(hass, device)
        with factory, pytest.raises(AbortFlow) as stopped:
            await f.async_step_zeroconf(zeroconf_info(ip_address=IPv4Address(address)))
        assert stopped.value.reason == "already_configured"
        assert existing.data == {"host": address, "discovery": "zeroconf"}
        f, factory = make_flow(hass, device)
        with factory, pytest.raises(AbortFlow):
            await f.async_step_dhcp(
                DhcpServiceInfo(
                    ip="192.0.2.2", hostname="better-ups-card", macaddress="020000000001"
                )
            )
        assert existing.data["host"] == address
    assert not device.writes


@pytest.mark.parametrize("sources", [("dhcp", "zeroconf"), ("zeroconf", "dhcp")])
async def test_discovery_sources_deduplicate_through_real_flow_manager(hass, sources):
    from homeassistant import loader

    loader.async_setup(hass)
    device = MockCard()
    _, factory = make_flow(hass, device)
    manager = hass.config_entries.flow
    data = {
        "zeroconf": zeroconf_info(),
        "dhcp": DhcpServiceInfo(
            ip="192.0.2.1", hostname="better-ups-card", macaddress="020000000001"
        ),
    }
    with factory, patch("homeassistant.config_entries.async_process_deps_reqs", return_value=None):
        first = await manager.async_init(
            DOMAIN, context={"source": sources[0]}, data=data[sources[0]]
        )
        second = await manager.async_init(
            DOMAIN, context={"source": sources[1]}, data=data[sources[1]]
        )
    assert first["step_id"] == "user"
    assert second["type"] == "abort" and second["reason"] == "already_in_progress"
    assert len(manager.async_progress()) == 1 and not device.writes
    manager.async_abort(first["flow_id"])


@pytest.mark.parametrize("seen", [False, True])
async def test_diagnostics_include_only_discovery_evidence(hass, seen):
    from homeassistant.helpers.discovery_flow import DiscoveryKey

    from custom_components.better_ups_card.diagnostics import async_get_config_entry_diagnostics

    keys = (
        {
            "zeroconf": (
                DiscoveryKey(domain="zeroconf", key=("_http._tcp.local.", "fixture"), version=1),
            )
        }
        if seen
        else {}
    )
    configured = make_entry(
        DOMAIN,
        {"host": "192.0.2.1", "discovery": "zeroconf" if seen else "unexpected-private-value"},
        DEVICE,
        discovery_keys=keys,
    )
    result = await async_get_config_entry_diagnostics(hass, configured)
    assert result == {
        "discovery": "zeroconf" if seen else "manual_or_dhcp",
        "zeroconf_discovery_seen": seen,
    }
    assert "192.0.2.1" not in str(result) and DEVICE not in str(result)
