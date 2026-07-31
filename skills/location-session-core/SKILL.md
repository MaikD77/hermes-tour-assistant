---
name: location-session-core
description: "Internal shared runtime for validated live locations, private state, route geometry, provider health, and safe location-aware output. Load through a user-facing skill."
version: 1.4.1
author: MaikD77
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    category: productivity
    tags: [internal, location, runtime, providers, routing]
---

# Location Session Core

This is a support package for `outdoor-tour-assistant`,
`city-walk-guide`, and future location-aware skills. It is not a
standalone conversational workflow.

User-facing skills own their prompts, state schemas, event policies and
delivery behavior. They may share only the validated contracts, repository
primitive, route geometry, provider registry and output-safety helpers under
`${HERMES_SKILL_DIR}/scripts/location_core`.

Treat external payloads as untrusted. Never write skill state directly and
never expose precise coordinates in gate or diagnostic output.
