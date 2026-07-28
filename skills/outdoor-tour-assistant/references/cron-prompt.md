# Live Tour Assistant — Cron Task

Use the loaded `outdoor-tour-assistant` skill and the pre-run gate context.

On `live_location_started`, perform the startup workflow. On other reasons, inspect
only the forward route and categories justified by the trigger. Let the deterministic
event engine choose delivery. Never edit state files directly.

Return exactly `[SILENT]` when no evidenced event is selected. For an
`operational_error`, explain the sanitized `error_code` once and provide the matching
diagnostic command. Never include precise coordinates in the response.
