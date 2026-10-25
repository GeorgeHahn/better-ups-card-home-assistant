# AI disclosure

OpenAI Codex assisted with integration design, implementation, tests,
documentation, packaging, and review. Validation uses real Home Assistant
2026.10.4 classes, an independent HTTP card simulator, Hypothesis properties,
and a seeded failure schedule. CI enforces lint, formatting, coverage, and secret
scanning. Live installation results are reported separately; CI does not use a
physical UPS or production credentials.

Human maintainers remain responsible for review and release decisions.
