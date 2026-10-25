# Contributing

Keep changes focused on Home Assistant discovery and onboarding. Native MQTT
owns telemetry and availability. Do not add UPS power controls to onboarding.

Use Python 3.14 and the pinned test dependencies:

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
.venv/bin/python tools/check_secrets.py
```

Tests use the real Home Assistant flow manager and device registry, production
HTTPX Digest authentication, and an independent simulated card. Property tests
cover IPv4 policy, credential limits, exact resource admission, and encoding.
A deterministic 256-step schedule (seed 20261101) checks rejected saves,
disconnections, resource exhaustion, and successful revision advancement.
Coverage includes branches and must remain at least 95%.

Tests use documentation-only IP addresses and synthetic credentials. Never commit
live credentials, tokens, private keys, `.storage`, downloaded device settings,
firmware binaries, or raw installation logs. Review the complete staged diff and
file list; automated secret scanning is an additional check, not a guarantee.
The scanner reports file and line only, never the detected value. Document narrow
false-positive exclusions inline with `pragma: allowlist secret` and explain them.

CI uses read-only repository permissions, pinned action commits, no project
secrets, and ordinary `pull_request` events. It does not deploy to Home Assistant.
Dependency updates require review and passing checks. Increment the integration
manifest version for releases and describe compatibility or migration changes.

Contributions are licensed MIT OR Apache-2.0. Disclose AI tools, their role, and
how changes were validated in the pull request. State "None" if none were used.
Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
