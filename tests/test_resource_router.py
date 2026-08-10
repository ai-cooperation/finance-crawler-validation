from pathlib import Path

import pytest

from finance_crawler_poc.resource_router import (
    Attempt,
    Executor,
    ExecutorState,
    ResourceDemand,
    RoutingBlocked,
    load_executors,
    select_executor,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


EXECUTORS = (
    Executor(
        id="cf_worker_http",
        platform="cloudflare",
        capabilities=frozenset({"http", "json", "rss", "static_html"}),
        max_duration_seconds=30,
        max_response_bytes=2_000_000,
        cost_rank=1,
    ),
    Executor(
        id="cf_browser_run",
        platform="cloudflare",
        capabilities=frozenset({"http", "javascript", "chromium"}),
        max_duration_seconds=60,
        max_response_bytes=5_000_000,
        cost_rank=2,
    ),
    Executor(
        id="github_browser",
        platform="github_actions",
        capabilities=frozenset({"http", "javascript", "chromium", "python"}),
        max_duration_seconds=1_200,
        max_response_bytes=20_000_000,
        cost_rank=3,
    ),
    Executor(
        id="browserless_residential",
        platform="commercial",
        capabilities=frozenset(
            {"http", "javascript", "chromium", "residential_egress"}
        ),
        max_duration_seconds=120,
        max_response_bytes=20_000_000,
        cost_rank=4,
        requires_credential=True,
    ),
)


def states(*, commercial: bool = False) -> dict[str, ExecutorState]:
    return {
        executor.id: ExecutorState(
            available=True,
            credential_available=(commercial or not executor.requires_credential),
            remaining_jobs=100,
        )
        for executor in EXECUTORS
    }


def test_router_uses_the_lowest_cost_executor_that_fits_current_demand() -> None:
    rss = ResourceDemand(
        required_capabilities=frozenset({"http", "rss"}),
        expected_duration_seconds=5,
        expected_response_bytes=200_000,
    )
    short_javascript = ResourceDemand(
        required_capabilities=frozenset({"http", "javascript"}),
        expected_duration_seconds=20,
        expected_response_bytes=500_000,
    )
    python_browser = ResourceDemand(
        required_capabilities=frozenset({"http", "javascript", "python"}),
        expected_duration_seconds=180,
        expected_response_bytes=3_000_000,
    )

    assert select_executor(rss, EXECUTORS, states()).id == "cf_worker_http"
    assert select_executor(short_javascript, EXECUTORS, states()).id == "cf_browser_run"
    assert select_executor(python_browser, EXECUTORS, states()).id == "github_browser"


def test_router_uses_runtime_state_and_never_assumes_commercial_credentials() -> None:
    demand = ResourceDemand(
        required_capabilities=frozenset(
            {"http", "javascript", "chromium", "residential_egress"}
        ),
        expected_duration_seconds=30,
        expected_response_bytes=1_000_000,
    )

    with pytest.raises(RoutingBlocked, match="no eligible executor"):
        select_executor(demand, EXECUTORS, states(commercial=False))

    assert (
        select_executor(demand, EXECUTORS, states(commercial=True)).id
        == "browserless_residential"
    )


def test_retryable_failure_excludes_the_attempted_executor() -> None:
    demand = ResourceDemand(
        required_capabilities=frozenset({"http", "javascript", "chromium"}),
        expected_duration_seconds=20,
        expected_response_bytes=500_000,
    )
    attempts = (Attempt(executor_id="cf_browser_run", outcome="timeout"),)

    assert select_executor(demand, EXECUTORS, states(), attempts=attempts).id == "github_browser"


@pytest.mark.parametrize("outcome", ["robots_denied", "auth_required", "paywall"])
def test_compliance_boundaries_stop_escalation(outcome: str) -> None:
    demand = ResourceDemand(
        required_capabilities=frozenset({"http"}),
        expected_duration_seconds=5,
        expected_response_bytes=100_000,
    )

    with pytest.raises(RoutingBlocked, match="terminal compliance outcome"):
        select_executor(
            demand,
            EXECUTORS,
            states(commercial=True),
            attempts=(Attempt(executor_id="cf_worker_http", outcome=outcome),),
        )


def test_repository_executor_catalog_is_resource_based_and_opt_in_for_commercial() -> None:
    executors = load_executors(REPOSITORY_ROOT / "resource-executors.yaml")
    by_id = {executor.id: executor for executor in executors}

    assert set(by_id) == {
        "browserless_residential",
        "cf_browser_run",
        "cf_worker_http",
        "firecrawl_hosted",
        "github_actions_crawl4ai",
    }
    assert by_id["cf_browser_run"].platform == "cloudflare"
    assert "javascript" in by_id["github_actions_crawl4ai"].capabilities
    assert {"json", "rss", "static_html"} <= by_id[
        "github_actions_crawl4ai"
    ].capabilities
    assert by_id["firecrawl_hosted"].requires_credential is True
    assert by_id["browserless_residential"].requires_credential is True


def test_executor_catalog_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "resource-executors.yaml"
    path.write_text(
        """
version: 1
executors:
  - &shared
    id: shared_executor
    platform: first
    capabilities: [http]
    max_duration_seconds: 30
    max_response_bytes: 1000
    cost_rank: 1
  - <<: *shared
    platform: second
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate executor id"):
        load_executors(path)
