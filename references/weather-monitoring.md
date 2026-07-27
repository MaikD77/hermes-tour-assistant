# Weather Monitoring for Tour Assistant

Companion recipe to the Weather Monitoring section in SKILL.md. Provides concrete search templates and deduplication guidance.

## Search Query Templates

Use `web_search` — web scraping (via `web_extract` or `browser_navigate`) is unreliable because weather portals use heavy JS/cookie walls that return truncated or empty content. A direct search consistently returns current conditions from search-result descriptions:

```
"Wetter [nearest town] heute aktuell Gewitter Böen"
"Wetter [nearest town] aktuell"
"Unwetterwarnung [county/region] [date]"
"DWD Warnung [town/county] aktuell"
```

## What to Extract

From the search result description text, locate:

| Field | Where to find it | Example |
|-------|-----------------|---------|
| Temperature range | Near the top of the hourly table | 30–31°C |
| Precipitation | `X %` or `X l/m²` near hourly rows | 0 %, 0 l/m² |
| Wind speed | `X km/h` after wind direction letter | 17 km/h |
| Gusts | `Böen X km/h` after wind speed | Böen 50 km/h |
| Warnings | "Derzeit liegen keine Unwetterwarnungen vor" or specific WarnLage text | none |

## Deduplication Key Pattern

```
weather_<phenomenon>_<date>_<scale>
```

Examples:

- `weather_gusts_2026-07-26_afternoon_48-58_kmh` — reported afternoon gusts
- `weather_gusts_2026-07-26_evening_up_to_71_kmh` — reported forecast evening gusts
- `weather_precipitation_2026-07-26_started` — rain started

## When NOT to Report

Do not re-announce weather unless one of these triggers fires:

| Trigger | Threshold |
|---------|-----------|
| New severe-weather warning | `Unwetter`, `amtliche Warnung`, `Starkregen`, `schwere Böen` |
| Gusts increased sharply | ≥20 km/h above the last reported value |
| Rain began | Last check had 0 mm, this check has ≥1 mm/h |
| Old check | Previous check >60 min ago AND a material change occurred |

A 2°C temperature swing or 5 km/h gust change between checks within 5–30 minutes is normal diurnal variation — ignore it.

## Example Persisted Weather Block

```json
"weather": {
  "checked_at": 1785078168,
  "temperature_c": 30.0,
  "precipitation_mm": 0.0,
  "wind_kmh": 23.5,
  "gusts_current_kmh": 46.8
}
```

## Provider Guidance

- **`web_search`** — primary source. Returns current conditions, wind/gust data, and active DWD warnings from search-result descriptions alone. No page body scraping needed.
- **`firecrawl_search`** — when available, but rate-limits aggressively on the free tier.
- **`web_extract` / browser tools** — **do not use** for weather portals. wetter.com, DWD, and AccuWeather pages require JS execution, cookie consent, and return truncated/empty content to direct scraping.