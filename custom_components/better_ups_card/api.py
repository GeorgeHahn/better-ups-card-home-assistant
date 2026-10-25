"""Authenticated, bounded configuration client. Never retry a POST automatically."""

import asyncio
import re
from ipaddress import IPv4Address

import httpx


class CardError(Exception):
    """A safe, translated error key; never includes a response body or credentials."""


def ipv4(value: str) -> str:
    address = IPv4Address(value)
    if int(address) < 0x01000000 or address.is_loopback or int(address) >= 0xE0000000:
        raise ValueError("A unicast LAN IPv4 address is required")
    return str(address)


def encode(values: dict[str, str | int]) -> bytes:
    """Firmware's unambiguous hex-line configuration representation."""
    return "".join(
        f"{key}={str(value).encode('utf-8').hex()}\n" for key, value in values.items()
    ).encode("ascii")


def admission(config: dict) -> None:
    """Use target-generated complete profiles, never host memory estimates."""
    try:
        p = config["protocols"]
        index = (
            int(any(p[v] for v in ("snmp_v1", "snmp_v2c", "snmp_v3")))
            + 2 * int(p["nut_enabled"] and config["nut_available"])
            + 4
        )
        required = config["memory"]["profiles"][index]
        if (
            type(required) is not int
            or required <= 0
            or type(config["memory"]["capacity"]) is not int
        ):
            raise CardError("unsupported_firmware")
        if required > config["memory"]["capacity"]:
            raise CardError("resources")
    except (KeyError, IndexError, TypeError) as error:
        raise CardError("unsupported_firmware") from error


def broker_fields(address: str, port: int, username: str, password: str) -> dict:
    try:
        address = ipv4(address)
        if (
            type(port) is not int
            or not 1 <= port <= 65535
            or len(username.encode()) > 24
            or len(password.encode()) > 32
        ):
            raise ValueError
        if not username and password or any(not 32 <= ord(c) < 127 for c in username + password):
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise CardError("broker_config") from error
    return {
        "mqtt_enabled": 1,
        "mqtt_discovery": 1,
        "mqtt_address": address,
        "mqtt_port": port,
        "mqtt_user": username,
        **({"mqtt_password": password} if password else {"mqtt_clear_password": 1}),
    }


class Card:
    """The flow owns this object; credentials are not stored in the config entry."""

    def __init__(self, host: str, username: str, password: str, *, transport=None):
        self.host = ipv4(host)
        self._auth = httpx.DigestAuth(username, password)
        self._transport = transport

    async def _request(self, method: str, **kwargs) -> httpx.Response:
        try:
            async with (
                asyncio.timeout(15),
                httpx.AsyncClient(
                    auth=self._auth,
                    timeout=10,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
            ):
                async with client.stream(
                    method, f"http://{self.host}/api/config", **kwargs
                ) as response:
                    # The firmware response is <= 2048 bytes. Bound hostile/wrong hosts too.
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=2048):
                        body.extend(chunk)
                        if len(body) > 8192:
                            raise CardError(
                                "save_uncertain" if method == "POST" else "unsupported_firmware"
                            )
                    return httpx.Response(response.status_code, content=bytes(body))
        except (httpx.HTTPError, TimeoutError) as error:
            raise CardError("save_uncertain" if method == "POST" else "cannot_connect") from error

    async def inspect(self) -> dict:
        response = await self._request("GET")
        if response.status_code == 401:
            raise CardError("invalid_auth")
        if response.status_code != 200:
            raise CardError("unsupported_firmware")
        try:
            config = response.json()
            if (
                not re.fullmatch(r"ups[0-9a-f]{12}", config["device_id"])
                or type(config["revision"]) is not int
                or not 0 <= config["revision"] <= 0xFFFFFFFF
            ):
                raise ValueError
            admission(config)
        except (ValueError, KeyError, TypeError) as error:
            raise CardError("unsupported_firmware") from error
        return config

    async def provision(self, device_id: str, values: dict) -> None:
        current = await self.inspect()
        if current["device_id"] != device_id:
            raise CardError("wrong_device")
        body = encode({"revision": current["revision"], **values})
        response = await self._request(
            "POST",
            content=body,
            headers={"Content-Type": "text/plain", "X-Requested-With": "better-ups-card"},
        )
        errors = {401: "invalid_auth", 409: "config_changed", 422: "resources"}
        if response.status_code in errors:
            raise CardError(errors[response.status_code])
        if response.status_code not in (200, 202):
            raise CardError("save_uncertain")
