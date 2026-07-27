from __future__ import annotations


def test_provider_runner_retries_and_recovers(providers_module) -> None:
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("temporary")
        return "ok"

    sleeps: list[float] = []
    runner = providers_module.ProviderRunner(
        "test", retries=2, backoff_seconds=0.5, sleep=sleeps.append
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
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected provider failure")

    assert runner.health.status == "degraded"
    assert runner.health.consecutive_failures == 1
    assert "ConnectionError" in runner.health.last_error


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
