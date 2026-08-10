from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


TERMINAL_COMPLIANCE_OUTCOMES = frozenset(
    {"robots_denied", "auth_required", "paywall"}
)
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class RoutingBlocked(RuntimeError):
    """Raised when policy or available resources prohibit execution."""


class ExecutorConfigError(ValueError):
    """Raised when the executor resource catalog is invalid."""


@dataclass(frozen=True)
class ResourceDemand:
    required_capabilities: frozenset[str]
    expected_duration_seconds: int
    expected_response_bytes: int
    max_cost_rank: int | None = None

    def __post_init__(self) -> None:
        if not self.required_capabilities:
            raise ValueError("required_capabilities must be non-empty")
        if self.expected_duration_seconds <= 0:
            raise ValueError("expected_duration_seconds must be positive")
        if self.expected_response_bytes <= 0:
            raise ValueError("expected_response_bytes must be positive")
        if self.max_cost_rank is not None and self.max_cost_rank < 0:
            raise ValueError("max_cost_rank must be non-negative")


@dataclass(frozen=True)
class Executor:
    id: str
    platform: str
    capabilities: frozenset[str]
    max_duration_seconds: int
    max_response_bytes: int
    cost_rank: int
    requires_credential: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.platform or not self.capabilities:
            raise ValueError("executor identity and capabilities are required")
        if self.max_duration_seconds <= 0 or self.max_response_bytes <= 0:
            raise ValueError("executor resource limits must be positive")
        if self.cost_rank < 0:
            raise ValueError("executor cost_rank must be non-negative")


@dataclass(frozen=True)
class ExecutorState:
    available: bool
    credential_available: bool
    remaining_jobs: int

    def __post_init__(self) -> None:
        if self.remaining_jobs < 0:
            raise ValueError("remaining_jobs must be non-negative")


@dataclass(frozen=True)
class Attempt:
    executor_id: str
    outcome: str


def load_executors(path: Path) -> tuple[Executor, ...]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ExecutorConfigError(f"cannot read executor catalog: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ExecutorConfigError("executor catalog root must be a mapping")
    if raw.get("version") != 1:
        raise ExecutorConfigError("executor catalog version must be 1")
    executor_items = raw.get("executors")
    if not isinstance(executor_items, list) or not executor_items:
        raise ExecutorConfigError("executors must be a non-empty list")

    executors: list[Executor] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(executor_items):
        executor = _parse_executor(item, index)
        if executor.id in seen_ids:
            raise ExecutorConfigError(f"duplicate executor id: {executor.id}")
        seen_ids.add(executor.id)
        executors.append(executor)
    return tuple(executors)


def select_executor(
    demand: ResourceDemand,
    executors: tuple[Executor, ...],
    states: dict[str, ExecutorState],
    *,
    attempts: tuple[Attempt, ...] = (),
) -> Executor:
    # INVARIANT: transports do not have platform owners. Select from current
    # task demand and runtime state so the same route can move between runners.
    terminal = next(
        (
            attempt.outcome
            for attempt in reversed(attempts)
            if attempt.outcome in TERMINAL_COMPLIANCE_OUTCOMES
        ),
        None,
    )
    if terminal is not None:
        raise RoutingBlocked(f"terminal compliance outcome: {terminal}")

    attempted_ids = {attempt.executor_id for attempt in attempts}
    candidates = [
        executor
        for executor in executors
        if executor.id not in attempted_ids
        and _fits_demand(executor, demand)
        and _has_runtime_capacity(executor, states.get(executor.id))
    ]
    if not candidates:
        raise RoutingBlocked("no eligible executor for current demand and runtime state")
    return min(
        candidates,
        key=lambda executor: (
            executor.cost_rank,
            executor.max_duration_seconds - demand.expected_duration_seconds,
            executor.max_response_bytes - demand.expected_response_bytes,
            executor.id,
        ),
    )


def _parse_executor(raw: Any, index: int) -> Executor:
    if not isinstance(raw, Mapping):
        raise ExecutorConfigError(f"executor {index} must be a mapping")
    executor_id = _required_config_string(raw, "id", index)
    platform = _required_config_string(raw, "platform", index)
    if not ID_PATTERN.fullmatch(executor_id):
        raise ExecutorConfigError(f"executor {executor_id} has invalid id")
    if not ID_PATTERN.fullmatch(platform):
        raise ExecutorConfigError(f"executor {executor_id} has invalid platform")

    capabilities_raw = raw.get("capabilities")
    if not isinstance(capabilities_raw, list) or not capabilities_raw:
        raise ExecutorConfigError(
            f"executor {executor_id} capabilities must be a non-empty list"
        )
    if not all(
        isinstance(capability, str) and ID_PATTERN.fullmatch(capability)
        for capability in capabilities_raw
    ):
        raise ExecutorConfigError(f"executor {executor_id} has invalid capability")
    if len(capabilities_raw) != len(set(capabilities_raw)):
        raise ExecutorConfigError(f"executor {executor_id} has duplicate capability")

    requires_credential = raw.get("requires_credential", False)
    if not isinstance(requires_credential, bool):
        raise ExecutorConfigError(
            f"executor {executor_id} requires_credential must be boolean"
        )
    return Executor(
        id=executor_id,
        platform=platform,
        capabilities=frozenset(capabilities_raw),
        max_duration_seconds=_required_positive_int(
            raw, "max_duration_seconds", executor_id
        ),
        max_response_bytes=_required_positive_int(
            raw, "max_response_bytes", executor_id
        ),
        cost_rank=_required_non_negative_int(raw, "cost_rank", executor_id),
        requires_credential=requires_credential,
    )


def _required_config_string(
    raw: Mapping[str, Any], key: str, index: int
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExecutorConfigError(
            f"executor {index} field {key} must be a non-empty string"
        )
    return value.strip()


def _required_positive_int(
    raw: Mapping[str, Any], key: str, executor_id: str
) -> int:
    value = _required_non_negative_int(raw, key, executor_id)
    if value == 0:
        raise ExecutorConfigError(f"executor {executor_id} {key} must be positive")
    return value


def _required_non_negative_int(
    raw: Mapping[str, Any], key: str, executor_id: str
) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExecutorConfigError(
            f"executor {executor_id} {key} must be a non-negative integer"
        )
    return value


def _fits_demand(executor: Executor, demand: ResourceDemand) -> bool:
    if not demand.required_capabilities.issubset(executor.capabilities):
        return False
    if demand.expected_duration_seconds > executor.max_duration_seconds:
        return False
    if demand.expected_response_bytes > executor.max_response_bytes:
        return False
    if demand.max_cost_rank is not None and executor.cost_rank > demand.max_cost_rank:
        return False
    return True


def _has_runtime_capacity(
    executor: Executor, state: ExecutorState | None
) -> bool:
    if state is None or not state.available or state.remaining_jobs == 0:
        return False
    if executor.requires_credential and not state.credential_available:
        return False
    return True
