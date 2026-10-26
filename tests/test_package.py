"""Check the installed HACS payload and translation contract."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "better_ups_card"


def test_hacs_package_and_translations():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert manifest["domain"] == "better_ups_card"
    assert manifest["zeroconf"] == [
        {"type": "_http._tcp.local.", "properties": {"product": "better-ups-card"}}
    ]
    assert manifest["requirements"] == ["httpx==0.28.1"]
    assert manifest["codeowners"] == ["@GeorgeHahn"]
    assert manifest["documentation"].endswith("/GeorgeHahn/better-ups-card-home-assistant")
    assert manifest["issue_tracker"] == manifest["documentation"] + "/issues"
    assert hacs["homeassistant"] == "2026.10.0"
    assert (COMPONENT / "brand" / "icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    strings = json.loads((COMPONENT / "strings.json").read_text())
    assert strings == json.loads((COMPONENT / "translations" / "en.json").read_text())
    for path in COMPONENT.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "CardError"
            ):
                for arg in node.args:
                    if isinstance(arg, ast.Constant):
                        assert arg.value in strings["config"]["error"]


def test_network_guard_rejects_real_connections_and_dns():
    import socket

    import pytest

    for family, target in [
        (socket.AF_INET, ("192.0.2.1", 80)),
        (socket.AF_INET6, ("2001:db8::1", 80)),
    ]:
        with socket.socket(family) as connection:
            with pytest.raises(AssertionError, match="network access is forbidden"):
                connection.connect(target)
            with pytest.raises(AssertionError, match="network access is forbidden"):
                connection.connect_ex(target)
        with socket.socket(family, socket.SOCK_DGRAM) as connection:
            with pytest.raises(AssertionError, match="network access is forbidden"):
                connection.sendto(b"test", target)
    with pytest.raises(AssertionError, match="network access is forbidden"):
        socket.getaddrinfo("broker.invalid", 1883)
