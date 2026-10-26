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
- The card's web administrator credentials. Setup displays the broker credential
  limits supported by the card's firmware.

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

Automatic discovery uses Home Assistant's mDNS/DNS-SD service, with DHCP as a
fallback for older firmware. Cards are identified by a stable device ID, so
hostname or address changes do not create duplicates.

Multicast must reach Home Assistant on the local network. Different subnets and
some container networks require multicast configuration or manual addition by
IPv4 address. No DHCP lease inspection is needed for DNS-SD discovery. Discovery
never writes card settings or sends credentials without completing the setup form.

## UPS data

Current card firmware advertises 49 native MQTT entities on the existing UPS
device. The original entity identifiers are preserved; updating firmware adds
new readings automatically without repeating onboarding. Older firmware exposes
its smaller set of entities.

| Data | Entities |
| --- | --- |
| Live measurements | Battery charge, runtime, battery voltage and temperature; input/output voltage and frequency; load and estimated output power |
| Status | Output source, battery status, time on battery, input failure count, alarms; on-battery, low-battery, output-enabled and alarm indicators |
| Battery information | Nominal voltage, capacity in Ah, pack count and exact pack configuration |
| Ratings and thresholds | Rated W/VA, nominal input/output voltage, voltage transfer points, input frequency limits and low-battery threshold |
| Tests and scheduling | Test result, elapsed time and detail; shutdown/startup countdowns, reboot duration and configured automatic restart |
| Identity | UPS manufacturer, model, firmware, name and attached-device text (including serial information when reported) |
| Optional diagnostics | Raw status, parameter 9/10, extra battery fields and pack configuration, encoded as hexadecimal |

Raw diagnostic entities are disabled by default. Enable them in the MQTT device's
entity list when needed. Test-result attributes also include the test OID and
start time in card uptime ticks; alarm attributes list active alarms with their
IDs and start times. These are card uptime values, not wall-clock timestamps.

Values depend on what the UPS reports. Missing readings show **Unknown**;
unsupported current and bypass measurements are not advertised as zero.
Electrical readings currently describe the first input/output line. Power is an
estimate from load percentage and a reported power rating. A compound battery
pack configuration such as `2x1` is preserved as text, without guessing its topology.
Inactive countdowns show Unknown. Test results and scheduled operations are
tracked by the card; configured automatic restart is the card's requested policy.

The card checks for changes at up to 1 Hz, sends changed topic groups, and
refreshes all readings every **60 seconds**. Initial connection and discovery
also send a complete snapshot. Native MQTT keeps `force_update` off, so unchanged
refreshes do not request duplicate entity-state writes. Data expires after
120 seconds without updates; communication loss is announced separately and the
broker's last will handles disconnection. Discovery is retained; expiring
measurement payloads are not, preventing old readings being replayed as fresh.
The card clears the old retained state from earlier firmware on connection.

UPS controls remain behind the card's web login, linked from the MQTT device.
No MQTT shutdown commands are installed.

## Credentials and network security

MQTT transport and the card configuration endpoint are unencrypted. HTTP Digest
protects the web password during authentication, but does not encrypt the broker
credentials in the configuration request. Broker credentials are stored in card
flash. Use a trusted LAN or isolated management network and preferably a dedicated
broker account. Do not expose either endpoint to the internet.

This integration retains the card address, stable device ID and discovery method
in its Home Assistant config entry. It does not duplicate broker credentials or retain
the web password there. Credentials exist transiently during onboarding and are
not included in errors or integration diagnostics. Native MQTT continues to store
its own broker credentials as usual.

## Troubleshooting and removal

Download integration diagnostics to check whether DNS-SD was observed and is the
preferred discovery method. This report contains no addresses, device IDs or
credentials and performs no network requests.

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

Use Python 3.14.2 or later in the 3.14 series:

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements-uv.txt
uv audit --locked
uv sync --locked
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest
```

Tests use simulated cards and synthetic MQTT messages; network access is blocked.
Shared mock data and factories live in `tests/helpers.py`, and Home Assistant
fixtures live in `tests/conftest.py`. Coverage includes branches and must reach 95%.
The offline MQTT contract checker also accepts synthetic firmware exports:

```sh
uv run --no-sync python tools/mqtt_contract.py /path/to/mock-discovery.json
```

Development dependencies are declared in `pyproject.toml` and pinned, including
transitive dependencies and artifact hashes, in `uv.lock`. Run `uv lock --upgrade`
to refresh them, then repeat the audit and checks above. Resolution and Dependabot
use a seven-day cooldown. CI audits the full lockfile against OSV before installing
dependencies, and reruns weekly to catch newly published advisories.

The dated cooldown exceptions in `pyproject.toml` retain the Home Assistant test
baseline and allow patched uv and certifi releases after the emSign certificate
incident. Remove each exception once that release is seven days old; until then,
its cutoff also prevents selecting a newer release of that package. When updating
uv, keep its dependency pin, `required-version`, and the bootstrap hashes in
`requirements-uv.txt` together.

The test environment overrides Home Assistant's Pillow 12.3.0 pin with
13.0.0 to address its image-decoder advisories. This intentionally differs from
upstream HA's dependency metadata: `uv pip check` reports that one mismatch.
It does not change users' HA installations.
No vulnerability IDs are ignored. An audit detects known advisories; it cannot
guarantee that a package is free of malicious code.

Licensed under MIT OR Apache-2.0; see [LICENSE](LICENSE).
AI assistance and validation are disclosed in [AI_DISCLOSURE.md](AI_DISCLOSURE.md).
