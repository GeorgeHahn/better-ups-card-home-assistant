"""Malformed peers must fail closed without exposing secrets or following redirects."""

import random
from unittest.mock import patch

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from custom_components.better_ups_card.api import (
    Card,
    CardError,
    admission,
    broker_fields,
    encode,
    ipv4,
)

from .helpers import DEVICE, MockCard, card_config


@pytest.mark.parametrize(
    "response,expected",
    [
        (httpx.Response(401), "invalid_auth"),
        (httpx.Response(302, headers={"location": "http://192.0.2.99/"}), "unsupported_firmware"),
        (httpx.Response(500), "unsupported_firmware"),
        (httpx.Response(200, content=b"broken"), "unsupported_firmware"),
        (httpx.Response(200, content=b"x" * 8193), "unsupported_firmware"),
        (httpx.Response(200, json=[]), "unsupported_firmware"),
    ],
)
async def test_bad_responses_are_safe(response, expected):
    requests = []

    def handler(request):
        requests.append(request)
        return response

    card = Card("192.0.2.1", "fixture", "fixture", transport=httpx.MockTransport(handler))
    with pytest.raises(CardError, match=f"^{expected}$"):
        await card.inspect()
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [httpx.ConnectError("fixture"), TimeoutError("fixture")])
async def test_connection_failures(failure):
    def handler(request):
        raise failure

    card = Card("192.0.2.1", "fixture", "fixture", transport=httpx.MockTransport(handler))
    with pytest.raises(CardError, match="^cannot_connect$"):
        await card.inspect()


async def test_oversize_save_response_is_uncertain_and_not_retried():
    posts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=card_config())
        posts.append(request)
        return httpx.Response(200, content=b"x" * 8193)

    card = Card("192.0.2.1", "fixture", "fixture", transport=httpx.MockTransport(handler))
    with pytest.raises(CardError, match="^save_uncertain$"):
        await card.provision(DEVICE, broker_fields("192.0.2.9", 1883, "", ""))
    assert len(posts) == 1


@pytest.mark.parametrize(
    "key,value",
    [
        ("device_id", "other"),
        ("revision", True),
        ("revision", -1),
        ("revision", 2**32),
        ("revision", "7"),
    ],
)
async def test_malformed_identity_and_revision(key, value):
    config = {**card_config(), key: value}
    card = Card(
        "192.0.2.1",
        "fixture",
        "fixture",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=config)),
    )
    with pytest.raises(CardError, match="^unsupported_firmware$"):
        await card.inspect()


@pytest.mark.parametrize(
    "memory",
    [
        None,
        {},
        {"profiles": []},
        {"profiles": [True] * 8, "capacity": 999},
        {"profiles": [0] * 8, "capacity": 999},
        {"profiles": [1] * 8, "capacity": "999"},
    ],
)
def test_malformed_memory_contract(memory):
    config = card_config()
    config["memory"] = memory
    with pytest.raises(CardError, match="^unsupported_firmware$"):
        admission(config)


@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_ipv4_matches_firmware_unicast_policy(value):
    from ipaddress import IPv4Address

    valid = 0x01000000 <= value < 0xE0000000 and value >> 24 != 127
    address = str(IPv4Address(value))
    if valid:
        assert ipv4(address) == address
    else:
        with pytest.raises(ValueError):
            ipv4(address)


@pytest.mark.parametrize(
    "port,user,password",
    [
        (True, "", ""),
        (0, "", ""),
        (65536, "", ""),
        (1883, "ha", "\n"),
        (1883, "é", ""),
        (1883, None, ""),
    ],
)
def test_invalid_broker_types(port, user, password):
    with pytest.raises(CardError, match="^broker_config$"):
        broker_fields("192.0.2.9", port, user, password)


@pytest.mark.parametrize(
    "limits",
    [
        None,
        {},
        {"username": True, "password": 32},
        {"username": 65, "password": 128},
        {"username": 64, "password": 129},
    ],
)
async def test_invalid_advertised_credential_limits(limits):
    config = {**card_config(), "mqtt_limits": limits}
    card = Card(
        "192.0.2.1",
        "fixture",
        "fixture",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=config)),
    )
    with pytest.raises(CardError, match="^unsupported_firmware$"):
        await card.inspect()


async def test_deterministic_resource_and_network_schedule():
    rng = random.Random(20261101)
    device = MockCard()
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
    config = card_config()
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


async def test_http_client_initialization_runs_off_the_event_loop():
    import threading

    from custom_components.better_ups_card import api

    threads = []
    constructor = httpx.AsyncClient

    def create(**kwargs):
        threads.append(threading.current_thread())
        return constructor(**kwargs)

    card = api.Card("192.0.2.1", "fixture", "fixture", transport=httpx.MockTransport(MockCard()))
    with patch.object(api.httpx, "AsyncClient", create):
        await card.inspect()
    assert len(threads) == 1
    assert threads[0] is not threading.current_thread()
