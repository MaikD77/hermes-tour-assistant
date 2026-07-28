from __future__ import annotations

import json


def test_provider_runner_retries_and_recovers(providers_module) -> None:
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("temporary")
        return "ok"

    sleeps: list[float] = []
    runner = providers_module.ProviderRunner(
        "test",
        retries=2,
        backoff_seconds=0.5,
        sleep=sleeps.append,
        jitter=lambda: 0,
    )

    assert runner.call(flaky, now=10.0) == "ok"
    assert attempts["count"] == 3
    assert sleeps == [0.5, 1.0]
    assert runner.health.status == "healthy"
    assert runner.health.consecutive_failures == 0


def test_provider_runner_exposes_degraded_health(providers_module) -> None:
    runner = providers_module.ProviderRunner("test", retries=0, sleep=lambda _: None)

    try:
        runner.call(lambda: (_ for _ in ()).throw(ConnectionError("offline")), now=10.0)
    except providers_module.ProviderCallError as error:
        assert error.code == "provider_unavailable"
    else:
        raise AssertionError("expected provider failure")

    assert runner.health.status == "degraded"
    assert runner.health.consecutive_failures == 1
    assert runner.health.last_error_code == "provider_unavailable"


def test_ttl_cache_expires_entries(providers_module) -> None:
    cache = providers_module.TTLCache()
    cache.set("weather", {"gust": 40}, now=10.0, ttl_seconds=60)

    assert cache.get("weather", 69.0) == {"gust": 40}
    assert cache.get("weather", 70.0) is None


def test_corridor_centers_are_forward_only(providers_module) -> None:
    points = [
        (50.0, 10.0, 0.0),
        (50.0, 10.03, 3_000.0),
        (50.0, 10.06, 6_000.0),
        (50.0, 10.10, 10_000.0),
        (50.0, 10.15, 15_000.0),
    ]

    centers = providers_module.corridor_search_centers(points, 2_000.0)

    assert [item[2] for item in centers] == [3_000, 6_000, 10_000]
    assert all(item[1] > 10.0 for item in centers)


def test_corridor_ranking_drops_behind_and_far_results(providers_module) -> None:
    results = [
        {"name": "behind", "distance_ahead_m": -100, "route_offset_m": 10},
        {"name": "far", "distance_ahead_m": 500, "route_offset_m": 900},
        {"name": "on-route", "distance_ahead_m": 2_000, "route_offset_m": 20},
        {"name": "detour", "distance_ahead_m": 1_000, "route_offset_m": 200},
    ]

    ranked = providers_module.rank_corridor_results(results)

    assert [item["name"] for item in ranked] == ["on-route", "detour"]


def test_permanent_provider_error_is_not_retried(providers_module) -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    def invalid() -> None:
        attempts["count"] += 1
        raise ValueError("untrusted raw provider text")

    runner = providers_module.ProviderRunner(
        "test",
        retries=3,
        sleep=sleeps.append,
        jitter=lambda: 0,
    )

    try:
        runner.call(invalid, now=10.0)
    except providers_module.ProviderCallError as error:
        assert error.code == "invalid_provider_response"
    else:
        raise AssertionError("expected normalized provider failure")
    assert attempts["count"] == 1
    assert sleeps == []
    assert runner.health.last_error_code == "invalid_provider_response"


def test_circuit_breaker_opens_after_repeated_failures(providers_module) -> None:
    runner = providers_module.ProviderRunner(
        "test",
        retries=2,
        sleep=lambda _: None,
        jitter=lambda: 0,
    )

    try:
        runner.call(lambda: (_ for _ in ()).throw(TimeoutError()), now=10.0)
    except providers_module.ProviderCallError:
        pass
    assert runner.health.circuit_open_until == 70.0

    try:
        runner.call(lambda: "unexpected", now=20.0)
    except providers_module.ProviderCallError as error:
        assert error.code == "provider_circuit_open"
    else:
        raise AssertionError("expected open circuit")


def test_provider_registry_resolves_capabilities(providers_module) -> None:
    registry = providers_module.ProviderRegistry()
    provider = object()
    registry.register("weather.current", "test", provider)

    assert registry.resolve("weather.current") is provider
    assert registry.capabilities() == {"weather.current": ("test",)}


def test_open_meteo_adapter_parses_structured_response(
    providers_module, monkeypatch
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return (
                b'{"current":{"temperature_2m":18.5,"precipitation":0.0,'
                b'"wind_speed_10m":12.0,"wind_gusts_10m":25.0}}'
            )

    monkeypatch.setattr(providers_module.urllib.request, "urlopen", lambda *_a, **_k: Response())
    provider = providers_module.OpenMeteoWeatherProvider()

    snapshot = provider.current_conditions(50.0, 10.0)

    assert snapshot.temperature_c == 18.5
    assert snapshot.gust_kmh == 25.0
    assert snapshot.source == provider.ENDPOINT


def test_directory_route_adapter_rejects_path_traversal(
    providers_module, tmp_path
) -> None:
    route = tmp_path / "planned.gpx"
    route.write_text("<gpx/>", encoding="utf-8")
    provider = providers_module.DirectoryRouteProvider(tmp_path)

    assert provider.list_planned_routes()[0].route_id == "planned.gpx"
    assert provider.download_route("planned.gpx") == route.resolve()

    link = tmp_path / "linked.gpx"
    link.symlink_to(route)
    for route_id, error_code in (
        ("../planned.gpx", "invalid_route_id"),
        ("linked.gpx", "route_not_found"),
    ):
        try:
            provider.download_route(route_id)
        except providers_module.ProviderCallError as error:
            assert error.code == error_code
        else:
            raise AssertionError("expected rejected route id")


def test_openstreetmap_reverse_adapter_normalizes_response(
    providers_module, monkeypatch
) -> None:
    payload = {
        "osm_type": "node",
        "osm_id": 123,
        "lat": "50.0",
        "lon": "10.0",
        "display_name": "Untrusted *name*",
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(payload).encode()

    monkeypatch.setattr(providers_module.urllib.request, "urlopen", lambda *_a, **_k: Response())
    provider = providers_module.OpenStreetMapProvider()

    result = provider.reverse_geocode(50.0, 10.0)

    assert result["source_id"] == "osm:node:123"
    assert result["name"] == "Untrusted *name*"
    assert result["lat"] == 50.0


def test_openstreetmap_corridor_adapter_whitelists_and_ranks(
    providers_module, monkeypatch
) -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 50.0001,
                "lon": 10.0001,
                "tags": {
                    "amenity": "drinking_water",
                    "name": "Quelle",
                    "drinking_water": "yes",
                },
            },
            {"type": "node", "id": 2, "lat": 50.02, "lon": 10.02, "tags": {}},
        ]
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(payload).encode()

    requests = []

    def urlopen(request, **_kwargs):
        requests.append(request)
        return Response()

    monkeypatch.setattr(providers_module.urllib.request, "urlopen", urlopen)
    provider = providers_module.OpenStreetMapProvider(radius_m=500)

    results = provider.search_corridor([(50.0, 10.0, 3_000.0)], ["water"])

    assert len(results) == 1
    assert results[0]["category"] == "water"
    assert results[0]["source_id"] == "osm:node:1"
    assert results[0]["tags"] == {"drinking_water": "yes"}
    assert b'amenity%22%3D%22drinking_water' in requests[0].data


def test_default_registry_exposes_structured_capabilities(
    providers_module, tmp_path
) -> None:
    registry = providers_module.build_default_registry(route_directory=tmp_path)

    assert registry.capabilities() == {
        "map.corridor": ("openstreetmap",),
        "map.reverse": ("openstreetmap",),
        "route.planned": ("local-gpx",),
        "water.search": ("openstreetmap",),
        "weather.current": ("open-meteo",),
    }


def test_structured_provider_validation_fails_closed(
    providers_module, monkeypatch, tmp_path
) -> None:
    missing_routes = providers_module.DirectoryRouteProvider(tmp_path / "missing")
    try:
        missing_routes.list_planned_routes()
    except providers_module.ProviderCallError as error:
        assert error.code == "route_directory_unavailable"
    else:
        raise AssertionError("expected missing route directory")

    routes = providers_module.DirectoryRouteProvider(tmp_path)
    try:
        routes.download_route("missing.gpx")
    except providers_module.ProviderCallError as error:
        assert error.code == "route_not_found"
    else:
        raise AssertionError("expected missing route")

    for operation in (
        lambda: providers_module.OpenStreetMapProvider(radius_m=10),
        lambda: providers_module.OpenStreetMapProvider().search_corridor([], ["water"]),
        lambda: providers_module.OpenStreetMapProvider().search_corridor(
            [(50.0, 10.0, 1_000.0)], ["unsupported"]
        ),
        lambda: providers_module.OpenStreetMapProvider().search_corridor(
            [(50.0, 10.0, -1.0)], ["water"]
        ),
    ):
        try:
            operation()
        except ValueError:
            pass
        else:
            raise AssertionError("expected invalid provider input")

    class EmptyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"{}"

    monkeypatch.setattr(
        providers_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: EmptyResponse(),
    )
    try:
        providers_module.OpenStreetMapProvider().search_corridor(
            [(50.0, 10.0, 1_000.0)], ["water"]
        )
    except ValueError as error:
        assert str(error) == "map response has no elements list"
    else:
        raise AssertionError("expected malformed map response")

    assert providers_module._matches_category("settlement", {"place": "town"})
    assert providers_module._matches_category("supply", {"shop": "supermarket"})
    assert providers_module._matches_category("hazard", {"hazard": "rockfall"})
    assert providers_module._matches_category("poi", {"tourism": "viewpoint"})
    assert not providers_module._matches_category("unknown", {})
