"""Native MQTT owns telemetry; validate that contract with entirely mocked input."""

import json
import random

import pytest

from tools.mqtt_contract import validate


def fixture(values):
    messages = []
    expected = {}
    for index, (domain, value, unit, device_class) in enumerate(values):
        key = f"reading_{index}"
        config = {
            "name": key,
            "unique_id": f"ups020000000001_{key}",
            "state_topic": f"better-ups/ups020000000001/{key}",
            "availability_topic": "better-ups/ups020000000001/availability",
            "value_template": "{{ value_json.value | default(none) }}",
            "expire_after": 120,
            "device": {"identifiers": ["ups020000000001"], "name": "Mock UPS"},
        }
        if unit:
            config["unit_of_measurement"] = unit
        if device_class:
            config["device_class"] = device_class
        messages.extend(
            [
                {
                    "topic": f"homeassistant/{domain}/ups020000000001/{key}/config",
                    "payload": json.dumps(config),
                    "retain": True,
                },
                {
                    "topic": config["state_topic"],
                    "payload": json.dumps({"value": value}),
                    "retain": False,
                },
            ]
        )
        expected[config["unique_id"]] = (
            {"ON": True, "OFF": False, None: None}[value]
            if domain == "binary_sensor"
            else None
            if value is None
            else str(value)
        )
    messages.append(
        {
            "topic": "better-ups/ups020000000001/availability",
            "payload": "online",
            "retain": True,
        }
    )
    return messages, expected


async def test_generated_native_mqtt_values_refresh_and_expire():
    rng = random.Random(20261103)
    values = [
        ("sensor", None, "°C", "temperature"),
        ("sensor", None, "s", "duration"),
        ("sensor", "", "", ""),
        ("sensor", 'Model "A" \\ newline\n Ω', "", ""),
        ("sensor", "2x1", "", ""),
        ("sensor", 9.5, "Ah", ""),
        ("sensor", 1500, "VA", "apparent_power"),
        ("sensor", 60.0, "Hz", "frequency"),
    ]
    for _ in range(64):
        values.append(("sensor", rng.uniform(-100, 100), "V", "voltage"))
        values.append(("binary_sensor", rng.choice(["ON", "OFF", None]), "", "problem"))
    messages, expected = fixture(values)
    assert await validate(messages) == expected


async def test_native_mqtt_rejects_incompatible_discovery():
    messages, _ = fixture([("sensor", 12, "V", "voltage")])
    config = json.loads(messages[0]["payload"])
    config["device_class"] = "not_a_real_device_class"
    messages[0]["payload"] = json.dumps(config)
    # Match the actual HA schema error, not any arbitrary exception in the rig.
    import probatio

    with pytest.raises(probatio.Invalid):
        await validate(messages)
