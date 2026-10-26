"""Offline compatibility rig for native Home Assistant MQTT telemetry.

Consumes mock discovery/state messages, including an optional external export
from the firmware test rig. Never connects to a card, MQTT broker, or HA server.
"""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.components.mqtt import binary_sensor, sensor
from homeassistant.components.mqtt.models import DATA_MQTT, ReceiveMessage
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def message(topic, payload):
    return ReceiveMessage(topic, payload, 0, False, topic, 0.0)


async def validate(messages):
    """Feed real HA entity implementations through mocked MQTT delivery."""
    with TemporaryDirectory(prefix="ups-mqtt-contract-") as directory:
        hass = HomeAssistant(directory)
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
        dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        hass.data[DATA_MQTT] = SimpleNamespace(
            client=SimpleNamespace(connected=True),
            debug_info_entities={},
            state_write_requests=SimpleNamespace(write_state_request=Mock()),
        )
        entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="mqtt",
            title="Mock MQTT",
            data={},
            options={},
            source="user",
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=[],
        )
        states = {m["topic"]: m["payload"] for m in messages if not m["topic"].endswith("/config")}
        entities = []
        results = {}
        device_ids = set()
        try:
            for item in messages:
                if not item["topic"].endswith("/config"):
                    continue
                domain = item["topic"].split("/")[1]
                assert domain in ("sensor", "binary_sensor")
                module = sensor if domain == "sensor" else binary_sensor
                config = module.DISCOVERY_SCHEMA(json.loads(item["payload"]))
                cls = sensor.MqttSensor if domain == "sensor" else binary_sensor.MqttBinarySensor
                entity = cls(hass, config, entry, None)
                entities.append(entity)
                entity.entity_id = f"{domain}.{entity.unique_id}"
                assert entity.unique_id not in results
                device_ids.update(config["device"]["identifiers"])
                assert config["expire_after"] == 120
                assert config["availability_topic"] in states
                assert config["state_topic"] in states
                # Network delivery alone is mocked; templates, units, state parsing,
                # availability and expiration are the real HA implementations.
                entity._availability_message_received(
                    message(config["availability_topic"], "online")
                )
                entity._state_message_received(
                    message(config["state_topic"], states[config["state_topic"]])
                )
                assert entity.available
                assert not entity.force_update
                entity._subscriptions = {}
                entity._prepare_subscribe_topics()
                hass.data[DATA_MQTT].debug_info_entities[entity.entity_id] = {
                    "subscriptions": {config["state_topic"]: {"messages": []}}
                }
                write_state = hass.data[DATA_MQTT].state_write_requests.write_state_request
                write_state.reset_mock()
                for _ in range(3):
                    entity._subscriptions["state_topic"]["msg_callback"](
                        message(config["state_topic"], states[config["state_topic"]])
                    )
                write_state.assert_not_called()
                value = entity.native_value if domain == "sensor" else entity.is_on
                results[entity.unique_id] = value
                if "json_attributes_topic" in config:
                    topic = config["json_attributes_topic"]
                    entity._attributes_message_received(message(topic, states[topic]))
                    assert entity.extra_state_attributes == json.loads(states[topic])
                    hass.data[DATA_MQTT].debug_info_entities[entity.entity_id]["subscriptions"][
                        topic
                    ] = {"messages": []}
                    write_state.reset_mock()
                    entity._message_callback(
                        entity._attributes_message_received,
                        {"_attr_extra_state_attributes"},
                        message(topic, states[topic]),
                    )
                    write_state.assert_not_called()
                for available in [False, True, False, True]:
                    entity._availability_message_received(
                        message(config["availability_topic"], "online" if available else "offline")
                    )
                    assert entity.available == available
                hass.data[DATA_MQTT].client.connected = False
                assert not entity.available
                hass.data[DATA_MQTT].client.connected = True
                assert entity.available
                with patch.object(entity, "async_write_ha_state"):
                    entity._expiration_trigger()
                    entity._value_is_expired()
                    assert not entity.available
                    entity._state_message_received(
                        message(config["state_topic"], states[config["state_topic"]])
                    )
                    assert entity.available
            assert len(device_ids) == 1
            return results
        finally:
            for entity in entities:
                if entity._expiration_trigger:
                    entity._expiration_trigger()
            await hass.async_stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    result = asyncio.run(validate(json.loads(args.fixture.read_text())))
    print(f"Validated {len(result)} native MQTT entities, availability, attributes and expiry")
