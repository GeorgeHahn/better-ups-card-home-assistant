# Better UPS Card for Home Assistant

Discover and configure a Better UPS Card on your local network. This integration
sets up MQTT on the card; Home Assistant's built-in MQTT integration supplies the
UPS device, sensors, and availability. No cloud service is required.

## Requirements

- Home Assistant 2026.10 or later and an already configured MQTT integration.
- Better UPS Card firmware with MQTT, `/api/config` device identification, and
  eight target-generated resource profiles. Complete the card's initial setup first.
- A plain TCP MQTT broker reachable from the card over IPv4, using the default
  `homeassistant` discovery prefix. TLS and WebSocket brokers are not supported.
- The card's web administrator credentials. Broker usernames are limited to
  24 printable ASCII bytes and passwords to 32 bytes by the firmware.

## Installation and setup

1. In HACS, open **Custom repositories**, add
   `https://github.com/GeorgeHahn/better-ups-card-home-assistant`, and select
   **Integration**. Download **Better UPS Card**, then restart Home Assistant.
2. Open **Settings → Devices & services**. Accept a discovered Better UPS Card,
   or select **Add integration → Better UPS Card** and enter its IPv4 address.
3. Enter the card's web administrator credentials.
4. Check the suggested broker address and port. Home Assistant's Mosquitto app
   address is translated to a LAN address reachable from the card. You may use
   the existing broker credentials or provide a dedicated MQTT account.
5. Review and confirm the credential transfer. The integration checks the card's
   exact memory admission profile and saves only MQTT settings. The card restarts;
   UPS output stays on. Allow up to a minute for the MQTT device and entities.

Automatic discovery uses the DHCP hostname `better-ups-card`. Home Assistant
must see the card's DHCP traffic; different subnets, static addresses, and some
container networks require manual addition. Discovery never writes settings or
sends credentials without completing the setup form.

The MQTT device provides battery charge, runtime, battery/input/output voltage,
load, power, frequency, temperature, status, alarms, on-battery state, and an alarm
indicator. Available measurements depend on the UPS. The firmware currently
reports the first input/output line. UPS controls remain behind the card's web
login, linked from the MQTT device. No MQTT shutdown commands are installed.

## Credentials and network security

MQTT transport and the card configuration endpoint are unencrypted. HTTP Digest
protects the web password during authentication, but does not encrypt the broker
credentials in the configuration request. Broker credentials are stored in card
flash. Use a trusted LAN or isolated management network and preferably a dedicated
broker account. Do not expose either endpoint to the internet.

This integration retains only the card address and stable device ID in its
Home Assistant config entry. It does not duplicate broker credentials or retain
the web password there. Credentials exist transiently during onboarding and are
not included in errors or integration diagnostics. Native MQTT continues to store
its own broker credentials as usual.

## Troubleshooting and removal

- **Insufficient resources:** disable an optional protocol in the card's web
  configuration. The integration uses the firmware's complete memory profiles;
  the firmware independently checks the save again.
- **Incompatible broker:** TLS, WebSockets, and custom discovery prefixes cannot
  be provisioned by this version. Configure a compatible broker first.
- **Save uncertain:** a connection may close after settings are committed. Wait
  for the card to restart and inspect its web configuration before retrying.
  Saves are never automatically retried.
- **No entities:** verify broker reachability, account permissions, and MQTT
  discovery. Successful setup means settings were accepted, not that the card
  has authenticated to the broker. Check the MQTT device for fresh readings.
- **Changed credentials/broker:** update MQTT settings in the card's web UI, or
  remove the onboarding entry and add it again. Reconfiguration is not automatic.
- **Uninstall:** remove the Better UPS Card entry, then uninstall through HACS and
  restart Home Assistant. This leaves card settings and native MQTT entities
  intact. To remove retained discovery records, turn off Home Assistant discovery
  in the card UI while keeping MQTT connected for at least a minute, then disable
  MQTT and remove its device if desired.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the test rig and security review rules.
This repository contains only the Home Assistant integration and its supporting
files. Firmware images, hardware designs, installation credentials, and private
validation logs belong elsewhere.

Licensed under MIT OR Apache-2.0; see [LICENSE](LICENSE).
AI assistance and validation are disclosed in [AI_DISCLOSURE.md](AI_DISCLOSURE.md).
