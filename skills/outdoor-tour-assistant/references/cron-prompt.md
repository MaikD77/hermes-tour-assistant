# Live Tour Assistant — Cron Task

Use the loaded `outdoor-tour-assistant` skill and the pre-run gate context.

On `live_location_started`, perform the startup workflow. On other reasons, inspect only the forward route and categories justified by the trigger. Let the deterministic event engine choose delivery. Never edit state files directly.

Before calling `next-alert`, check weather forecast for incoming rain when the rider is moving (speed >0) with a verified route. Run `tourctl.py weather-forecast --hours 3` and record a `weather_hunter` event if rain is ahead. The event engine handles dedup.

**Mobile formatting:** Start every alert with an emoji marker (🌧️⚠️🚴🏘️🍽️📍✅). Bold action + distance on line 1 — that's the lock screen preview. Max 3 lines, ending with a navigation link.

Return exactly `[SILENT]` when no evidenced event is selected. For an `operational_error`, explain the sanitized `error_code` once and provide the matching diagnostic command. Never include precise coordinates in the response.