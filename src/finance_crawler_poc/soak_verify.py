from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from finance_crawler_poc.contracts import ContractValidationError, validate_contract


EXPECTED_REPOSITORY = "ai-cooperation/finance-crawler-validation"
EXPECTED_WORKFLOW = ".github/workflows/topic-radar.yml"
USAGE_FIELDS = (
    "github_actions_seconds",
    "worker_requests",
    "d1_rows_read",
    "d1_rows_written",
    "r2_class_a_operations",
    "r2_class_b_operations",
)
D1_COUNTER_FIELDS = (
    "runs",
    "published_runs",
    "raw_items",
    "topic_snapshots",
    "audit_events",
    "run_admissions",
    "operational_alerts",
    "open_alerts",
)


class SoakVerificationError(ValueError):
    """Raised when the seven-day soak evidence cannot prove the P0 gate."""


def verify_soak_window(payload: object) -> dict[str, int | bool]:
    bundle = _mapping(payload, "soak bundle")
    if bundle.get("schema_version") != 1:
        raise SoakVerificationError("soak bundle schema_version must be 1")
    if bundle.get("repository") != EXPECTED_REPOSITORY:
        raise SoakVerificationError(f"repository must be {EXPECTED_REPOSITORY}")
    if bundle.get("workflow") != EXPECTED_WORKFLOW:
        raise SoakVerificationError(f"workflow must be {EXPECTED_WORKFLOW}")

    window_start = _timestamp(bundle.get("window_started_at"), "window_started_at")
    window_end = _timestamp(bundle.get("window_ended_at"), "window_ended_at")
    if window_end - window_start != timedelta(days=7):
        raise SoakVerificationError("soak window must span exactly 7 UTC days")
    if window_start.time() != datetime.min.time() or window_end.time() != datetime.min.time():
        raise SoakVerificationError("soak window boundaries must be UTC midnight")

    source_ids = _string_list(bundle.get("expected_source_ids"), "expected_source_ids")
    if not source_ids or len(source_ids) != len(set(source_ids)):
        raise SoakVerificationError("expected_source_ids must be a non-empty unique list")
    ceilings = _usage_values(bundle.get("resource_ceilings"), "resource_ceilings")
    _verify_human_alerts(bundle.get("human_alert_validation"), window_start)

    days = _list(bundle.get("days"), "days")
    if len(days) != 7:
        raise SoakVerificationError("soak bundle must contain exactly 7 daily records")
    expected_dates = [window_start.date() + timedelta(days=index) for index in range(7)]
    actual_dates = [_date(_mapping(item, "day").get("date"), "day.date") for item in days]
    if actual_dates != expected_dates:
        raise SoakVerificationError("daily records must use consecutive UTC dates")

    workflow_run_ids: set[str] = set()
    previous_counts: dict[str, int] | None = None
    source_observations = 0
    for day_value, expected_day in zip(days, expected_dates, strict=True):
        day = _mapping(day_value, f"day {expected_day.isoformat()}")
        github = _mapping(day.get("github"), "github evidence")
        observation = _mapping(day.get("observation"), "soak observation")
        report = _mapping(day.get("source_report"), "source report")
        usage = _mapping(day.get("usage"), "usage evidence")

        workflow_run_id, commit_sha, run_attempt = _verify_github(github, expected_day)
        if workflow_run_id in workflow_run_ids:
            raise SoakVerificationError("daily records must use unique GitHub workflow run IDs")
        workflow_run_ids.add(workflow_run_id)
        counts = _verify_observation(
            observation,
            workflow_run_id,
            commit_sha,
            run_attempt,
            expected_day,
        )
        _verify_monotonic_counts(previous_counts, counts)
        previous_counts = counts
        _verify_source_report(report, observation, source_ids)
        source_observations += len(source_ids)
        _verify_usage(usage, ceilings, expected_day)

    return {
        "accepted": True,
        "days_verified": len(days),
        "scheduled_runs": len(workflow_run_ids),
        "published_runs": len(days),
        "source_observations": source_observations,
    }


def _verify_human_alerts(value: object, window_start: datetime) -> None:
    evidence = _mapping(value, "human_alert_validation")
    primary = _non_empty_string(evidence.get("primary_provider"), "primary_provider")
    fallback = _non_empty_string(evidence.get("fallback_provider"), "fallback_provider")
    if primary == fallback or evidence.get("different_failure_domain") is not True:
        raise SoakVerificationError("alert providers must use different failure domains")
    primary_received = _timestamp(evidence.get("primary_received_at"), "primary_received_at")
    fallback_received = _timestamp(evidence.get("fallback_received_at"), "fallback_received_at")
    if primary_received > window_start or fallback_received > window_start:
        raise SoakVerificationError("human alert delivery must be verified before the soak window")
    _non_empty_string(evidence.get("confirmation_reference"), "confirmation_reference")


def _verify_github(evidence: Mapping[str, object], expected_day: date) -> tuple[str, str, int]:
    workflow_run_id = _digits(evidence.get("workflow_run_id"), "GitHub workflow_run_id")
    commit_sha = _sha(evidence.get("commit_sha"), 40, "GitHub commit_sha")
    if evidence.get("event_name") != "schedule":
        raise SoakVerificationError("GitHub event_name must be schedule")
    if evidence.get("conclusion") != "success":
        raise SoakVerificationError("GitHub conclusion must be success")
    run_attempt = _non_negative_integer(evidence.get("run_attempt"), "GitHub run_attempt")
    if run_attempt < 1:
        raise SoakVerificationError("GitHub run_attempt must be positive")
    started = _timestamp(evidence.get("started_at"), "GitHub started_at")
    completed = _timestamp(evidence.get("completed_at"), "GitHub completed_at")
    if started.date() != expected_day or completed < started:
        raise SoakVerificationError("GitHub timestamps do not match the daily record")
    duration = _non_negative_integer(evidence.get("duration_seconds"), "GitHub duration_seconds")
    if duration != int((completed - started).total_seconds()):
        raise SoakVerificationError("GitHub duration_seconds does not match timestamps")
    if evidence.get("source") != "github_api":
        raise SoakVerificationError("GitHub evidence source must be github_api")
    return workflow_run_id, commit_sha, run_attempt


def _verify_observation(
    observation: Mapping[str, object],
    workflow_run_id: str,
    commit_sha: str,
    run_attempt: int,
    expected_day: date,
) -> dict[str, int]:
    if observation.get("workflow_run_id") != workflow_run_id:
        raise SoakVerificationError("observation workflow_run_id must match GitHub")
    if observation.get("commit_sha") != commit_sha:
        raise SoakVerificationError("observation commit_sha must match GitHub")
    observation_attempt = _non_negative_integer(
        observation.get("run_attempt"), "observation run_attempt"
    )
    if observation_attempt != run_attempt:
        raise SoakVerificationError("observation run_attempt must match GitHub")
    if _timestamp(observation.get("observed_at"), "observation observed_at").date() != expected_day:
        raise SoakVerificationError("observation must be recorded on the daily UTC date")
    if observation.get("replayed") is not False:
        raise SoakVerificationError("stored daily observation must be the original receipt")

    admission = _mapping(observation.get("admission"), "observation admission")
    if admission.get("decision") != "admitted" or admission.get("reason") != "admitted":
        raise SoakVerificationError("scheduled admission must be admitted")
    scheduled_run = _mapping(observation.get("scheduled_run"), "observation scheduled_run")
    if scheduled_run.get("state") != "published":
        raise SoakVerificationError("scheduled run must be published")
    if scheduled_run.get("current_snapshot_matches") is not True:
        raise SoakVerificationError("scheduled run must match current_snapshot")
    if _non_negative_integer(scheduled_run.get("item_count"), "scheduled item_count") < 1:
        raise SoakVerificationError("published scheduled run must contain at least one item")

    status = _mapping(observation.get("status"), "observation status")
    freshness = _mapping(status.get("freshness"), "observation freshness")
    if freshness.get("state") != "healthy":
        raise SoakVerificationError("snapshot freshness must be healthy")
    current = _mapping(status.get("current_snapshot"), "current_snapshot")
    if current.get("run_id") != scheduled_run.get("run_id"):
        raise SoakVerificationError("status run_id must match scheduled run")
    if current.get("snapshot_id") != scheduled_run.get("snapshot_id"):
        raise SoakVerificationError("status snapshot_id must match scheduled run")

    counts_value = _mapping(observation.get("d1_counts"), "d1_counts")
    counts = {
        field: _non_negative_integer(counts_value.get(field), f"d1_counts.{field}")
        for field in D1_COUNTER_FIELDS
    }
    if counts["open_alerts"] != 0:
        raise SoakVerificationError("open alert count must be zero")

    integrity = _mapping(observation.get("r2_integrity"), "r2_integrity")
    if integrity.get("all_metadata_match") is not True:
        raise SoakVerificationError("R2 metadata integrity must match D1")
    samples = _list(integrity.get("samples"), "R2 samples")
    if not 1 <= len(samples) <= 4 or integrity.get("checked_objects") != len(samples):
        raise SoakVerificationError("R2 integrity must contain 1 to 4 checked samples")
    topic_hashes = {
        sample.get("content_sha256")
        for value in samples
        if (sample := _mapping(value, "R2 sample")).get("kind") == "topic"
    }
    if topic_hashes != {current.get("content_sha256")}:
        raise SoakVerificationError("R2 topic metadata must match current snapshot hash")

    try:
        validate_contract("soak-observation", dict(observation))
    except ContractValidationError as exc:
        raise SoakVerificationError(f"invalid soak observation contract: {exc}") from exc
    return counts


def _verify_monotonic_counts(
    previous: Mapping[str, int] | None,
    current: Mapping[str, int],
) -> None:
    if previous is None:
        return
    for field in D1_COUNTER_FIELDS:
        if current[field] < previous[field]:
            raise SoakVerificationError(f"D1 counters must be monotonic: {field}")
    for field in ("runs", "published_runs", "topic_snapshots", "run_admissions"):
        if current[field] <= previous[field]:
            raise SoakVerificationError(f"daily published counters must increase: {field}")


def _verify_source_report(
    report: Mapping[str, object],
    observation: Mapping[str, object],
    expected_source_ids: list[str],
) -> None:
    if report.get("schema_version") != 1 or report.get("accepted") is not True:
        raise SoakVerificationError("source report must be accepted")
    scheduled_run = _mapping(observation.get("scheduled_run"), "scheduled_run")
    if report.get("run_id") != scheduled_run.get("run_id"):
        raise SoakVerificationError("source report run_id must match scheduled run")
    if report.get("snapshot_id") != scheduled_run.get("snapshot_id"):
        raise SoakVerificationError("source report snapshot_id must match scheduled run")
    total = _non_negative_integer(report.get("total_sources"), "source report total_sources")
    successful = _non_negative_integer(
        report.get("successful_sources"), "source report successful_sources"
    )
    minimum = _non_negative_integer(
        report.get("minimum_successful_sources"), "minimum_successful_sources"
    )
    if total != len(expected_source_ids) or successful < minimum:
        raise SoakVerificationError("source report totals do not meet the manifest gate")
    if report.get("topics") != 3:
        raise SoakVerificationError("accepted source report must contain exactly 3 topics")

    source_results = [_mapping(value, "source result") for value in _list(
        report.get("source_results"), "source_results"
    )]
    result_ids = [_non_empty_string(result.get("source_id"), "source result source_id")
                  for result in source_results]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(expected_source_ids):
        raise SoakVerificationError("source report must cover the exact manifest source set")
    calculated_success = 0
    for result in source_results:
        if result.get("status") == "success":
            calculated_success += 1
        elif result.get("status") != "failed":
            raise SoakVerificationError("source result status must be success or failed")
        if result.get("transport") not in {"rss", "json_api", "browser"}:
            raise SoakVerificationError("source result transport is invalid")
        _non_empty_string(result.get("route"), "source result route")
        request_url = _non_empty_string(result.get("request_url"), "source result request_url")
        if not request_url.startswith(("https://", "http://")):
            raise SoakVerificationError("source result request_url must be HTTP(S)")
        _non_negative_integer(result.get("item_count"), "source result item_count")
    if calculated_success != successful:
        raise SoakVerificationError("source report successful_sources does not match results")

    checkpoints = [_mapping(value, "checkpoint") for value in _list(
        report.get("checkpoints"), "checkpoints"
    )]
    checkpoint_ids = [_non_empty_string(value.get("source_id"), "checkpoint source_id")
                      for value in checkpoints]
    if len(checkpoint_ids) != len(set(checkpoint_ids)) or set(checkpoint_ids) != set(
        expected_source_ids
    ):
        raise SoakVerificationError("checkpoints must cover the exact manifest source set")


def _verify_usage(
    value: Mapping[str, object],
    ceilings: Mapping[str, int],
    expected_day: date,
) -> None:
    if value.get("github_source") != "github_api":
        raise SoakVerificationError("usage GitHub source must be github_api")
    if value.get("cloudflare_source") != "cloudflare_graphql":
        raise SoakVerificationError("usage Cloudflare source must be cloudflare_graphql")
    started = _timestamp(value.get("window_started_at"), "usage window_started_at")
    ended = _timestamp(value.get("window_ended_at"), "usage window_ended_at")
    captured = _timestamp(value.get("captured_at"), "usage captured_at")
    if started.date() != expected_day or ended - started != timedelta(days=1):
        raise SoakVerificationError("usage evidence must cover one exact UTC day")
    if captured < ended:
        raise SoakVerificationError("usage evidence must be captured after its UTC day")
    values = _usage_values(value, "usage evidence")
    for field in USAGE_FIELDS:
        if values[field] > ceilings[field]:
            raise SoakVerificationError(f"resource ceiling exceeded: {field}")


def _usage_values(value: object, name: str) -> dict[str, int]:
    mapping = _mapping(value, name)
    return {field: _non_negative_integer(mapping.get(field), f"{name}.{field}")
            for field in USAGE_FIELDS}


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SoakVerificationError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise SoakVerificationError(f"{name} must be a list")
    return value


def _string_list(value: object, name: str) -> list[str]:
    values = _list(value, name)
    return [_non_empty_string(item, name) for item in values]


def _non_empty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SoakVerificationError(f"{name} must be a non-empty string")
    return value


def _digits(value: object, name: str) -> str:
    text = _non_empty_string(value, name)
    if not text.isdigit():
        raise SoakVerificationError(f"{name} must contain only digits")
    return text


def _sha(value: object, length: int, name: str) -> str:
    text = _non_empty_string(value, name)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise SoakVerificationError(f"{name} must be a lowercase hexadecimal digest")
    return text


def _non_negative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SoakVerificationError(f"{name} must be a non-negative integer")
    return value


def _timestamp(value: object, name: str) -> datetime:
    text = _non_empty_string(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SoakVerificationError(f"{name} must be RFC 3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SoakVerificationError(f"{name} must use UTC")
    return parsed.astimezone(timezone.utc)


def _date(value: object, name: str) -> date:
    text = _non_empty_string(value, name)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SoakVerificationError(f"{name} must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a seven-day P0 soak evidence bundle")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.evidence.read_text(encoding="utf-8"))
        result = verify_soak_window(payload)
    except (OSError, json.JSONDecodeError, SoakVerificationError) as exc:
        print(json.dumps({"accepted": False, "error": str(exc)}, sort_keys=True))
        return 2
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(f"{args.output.suffix}.tmp")
        temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(args.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
