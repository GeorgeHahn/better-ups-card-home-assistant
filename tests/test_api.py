"""Malformed peers must fail closed without exposing secrets or following redirects."""

import copy

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_onboarding import CONFIG

from custom_components.better_ups_card.api import Card, CardError, admission, broker_fields, ipv4


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
            return httpx.Response(200, json=CONFIG)
        posts.append(request)
        return httpx.Response(200, content=b"x" * 8193)

    card = Card("192.0.2.1", "fixture", "fixture", transport=httpx.MockTransport(handler))
    with pytest.raises(CardError, match="^save_uncertain$"):
        await card.provision(CONFIG["device_id"], broker_fields("192.0.2.9", 1883, "", ""))
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
    config = {**CONFIG, key: value}
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
    config = copy.deepcopy(CONFIG)
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
