"""Discover a card, authenticate, then explicitly provision native MQTT discovery."""

import re
import socket
from ipaddress import IPv4Address

import probatio as vol
from homeassistant import config_entries
from homeassistant.components import network
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from . import DOMAIN
from .api import Card, CardError, broker_fields, credential_limits, ipv4

PASSWORD = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


class BetterUpsFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """No credentials survive completion; only host and stable device ID persist."""

    VERSION = 1

    def __init__(self):
        self._host = ""
        self._discovered_id = None
        self._card = None
        self._device_id = ""
        self._broker = {}
        self._limits = (24, 32)
        self._zeroconf = False

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        """Use HA's shared DNS-SD discovery and its most recently updated address."""
        properties = discovery_info.properties
        identity = properties.get("id")
        if (
            discovery_info.type != "_http._tcp.local."
            or discovery_info.port != 80
            or properties.get("product") != "better-ups-card"
            or not isinstance(identity, str)
            or re.fullmatch(r"ups[0-9a-f]{12}", identity) is None
        ):
            return self.async_abort(reason="invalid_discovery")
        try:
            host = ipv4(discovery_info.host)
        except ValueError:
            return self.async_abort(reason="invalid_discovery")
        self._zeroconf = True
        return await self._discovered(host, identity)

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo):
        mac = discovery_info.macaddress.replace(":", "").replace("-", "").lower()
        return await self._discovered(discovery_info.ip, f"ups{mac}")

    async def _discovered(self, host, identity):
        self._host = host
        self._discovered_id = identity
        existing = await self.async_set_unique_id(identity)
        updates = {CONF_HOST: host}
        if self._zeroconf:
            updates["discovery"] = "zeroconf"
        elif existing and existing.data.get("discovery") == "zeroconf":
            # A stale DHCP broadcast must not replace a DNS-SD-resolved address.
            updates = None
        self._abort_if_unique_id_configured(updates=updates)
        self.context["title_placeholders"] = {"host": self._host}
        return await self.async_step_user()

    async def _broker_defaults(self):
        entries = self.hass.config_entries.async_entries("mqtt")
        if len(entries) != 1:
            raise CardError("mqtt_required")
        config = {**entries[0].data, **entries[0].options}
        if (
            config.get("transport", "tcp") != "tcp"
            or config.get("certificate")
            or config.get("client_cert")
            or config.get("tls_insecure")
            or config.get("port", 1883) == 8883
            or config.get("discovery_prefix", "homeassistant") != "homeassistant"
            or not config.get("discovery", True)
        ):
            raise CardError("mqtt_incompatible")
        host = config.get("broker", "")
        if host in ("core-mosquitto", "localhost", "127.0.0.1"):
            host = await network.async_get_source_ip(self.hass, target_ip=self._host)
        else:
            try:
                IPv4Address(host)
            except ValueError:
                records = await self.hass.async_add_executor_job(
                    socket.getaddrinfo, host, None, socket.AF_INET, socket.SOCK_STREAM
                )
                host = records[0][4][0]
        self._broker = {
            "address": ipv4(host),
            "port": config.get("port", 1883),
            "username": config.get("username", ""),
            "password": config.get("password", ""),
        }

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self._host = ipv4(user_input[CONF_HOST])
                card = Card(self._host, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                config = await card.inspect()
                if self._discovered_id and config["device_id"] != self._discovered_id:
                    raise CardError("wrong_device")
                self._limits = credential_limits(config)
                self._device_id = config["device_id"]
                # Manual setup may claim an already discovered card, as HA's
                # native integrations do. Existing configured cards still abort.
                await self.async_set_unique_id(self._device_id, raise_on_progress=False)
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
                self._card = card
                await self._broker_defaults()
                return await self.async_step_broker()
            except CardError as error:
                errors["base"] = str(error)
            except ValueError, OSError, IndexError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self._host): str,
                    vol.Required(CONF_USERNAME, default="admin"): str,
                    vol.Required(CONF_PASSWORD): PASSWORD,
                }
            ),
            errors=errors,
        )

    async def async_step_broker(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                if not user_input["confirm_plaintext"]:
                    raise CardError("confirm_required")
                existing = user_input["use_existing_credentials"]
                values = broker_fields(
                    user_input["address"],
                    user_input["port"],
                    self._broker["username"] if existing else user_input.get("username", ""),
                    self._broker["password"] if existing else user_input.get("password", ""),
                    *self._limits,
                )
                self._abort_if_unique_id_configured()
                await self._card.provision(self._device_id, values)
                self._card = None
                self._broker.clear()
                return self.async_create_entry(
                    title=f"UPS {self._device_id}",
                    data={
                        CONF_HOST: self._host,
                        **({"discovery": "zeroconf"} if self._zeroconf else {}),
                    },
                )
            except CardError as error:
                errors["base"] = str(error)
        return self.async_show_form(
            step_id="broker",
            data_schema=vol.Schema(
                {
                    vol.Required("address", default=self._broker["address"]): str,
                    vol.Required("port", default=self._broker["port"]): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=65535)
                    ),
                    vol.Required("use_existing_credentials", default=True): bool,
                    vol.Optional("username", default=""): str,
                    vol.Optional("password", default=""): PASSWORD,
                    vol.Required("confirm_plaintext", default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "host": self._host,
                "username_max": str(self._limits[0]),
                "password_max": str(self._limits[1]),
            },
        )
