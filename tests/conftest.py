"""Tests must never contact a card, broker, or other network service."""

import asyncio
import socket

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .helpers import TEST_BROKER_PASSWORD, make_entry


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Allow asyncio's local wakeup socket; forbid IP connections and DNS."""

    def blocked(*args, **kwargs):
        raise AssertionError("Tests must use mocks; network access is forbidden")

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        original = getattr(socket.socket, name)

        def guarded(sock, *args, _original=original, **kwargs):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                blocked()
            return _original(sock, *args, **kwargs)

        monkeypatch.setattr(socket.socket, name, guarded)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
async def hass(tmp_path):
    """Provide a fresh HA instance with a synthetic MQTT config entry."""
    instance = HomeAssistant(str(tmp_path))
    try:
        instance.config_entries = ConfigEntries(instance, {})
        await asyncio.wait_for(instance.config_entries.async_initialize(), 5)
        dr.async_setup(instance)
        await asyncio.wait_for(dr.async_load(instance), 5)
        mqtt_entry = make_entry(
            "mqtt",
            {
                "broker": "192.0.2.9",
                "port": 1883,
                "username": "ha",
                "password": TEST_BROKER_PASSWORD,
            },
        )
        instance.config_entries._entries[mqtt_entry.entry_id] = mqtt_entry
        yield instance
    finally:
        await instance.async_stop()
