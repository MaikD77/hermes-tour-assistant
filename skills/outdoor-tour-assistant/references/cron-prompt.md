Minimales, skill-gestütztes Cron-Task-Prompt für den Live Tour Assistant.

Bevor du `next-alert` aufrufst:
**Weather Hunter:** Vor `next-alert` die Vorhersage prüfen, wenn der Rider mit einer verifizierten Route unterwegs ist (speed > 0). `tourctl.py weather-forecast --hours 3` ausführen und ein `weather_hunter`-Event aufzeichnen, wenn Regen ≥0.3 mm/h bevorsteht. Die Event Engine dedupliziert — derselbe Regen wiederholt sich nicht innerhalb der Cooldown-Zeit.

**Mobile formatting:** Starte jeden Alert mit einem Emoji-Marker (🌧️⚠️🚴🏘️🍽️📍✅). Bold Action + Distance in Zeile 1 — das ist die Lock-Screen-Vorschau. Max 3 Zeilen, endend mit einem Navigationslink. Keine präzisen Koordinaten oder internen State-Keys verwenden.

Nach `[SILENT]` keine Ausgabe.