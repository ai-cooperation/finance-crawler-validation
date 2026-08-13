from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

import httpx

from finance_crawler_poc.soak_usage import UsageCollectionError, build_daily_usage


GITHUB_REPOSITORY = "ai-cooperation/finance-crawler-validation"
CLOUDFLARE_ACCOUNT_ID = "ca985c195ab218488fc0744692dbde21"
WORKER_SCRIPT = "finance-crawler-validation-ingest"
D1_DATABASE_ID = "476bd84f-e924-4b9b-a9d9-dfca9ea29a1a"
R2_BUCKET = "finance-crawler-validation-raw"
GITHUB_API = "https://api.github.com"
CLOUDFLARE_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

CLOUDFLARE_QUERY = """
query DailySoakUsage(
  $accountTag: string!
  $datetimeStart: Time!
  $datetimeEnd: Time!
  $date: Date!
  $scriptName: string!
  $databaseId: string!
  $bucketName: string!
) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      workers: workersInvocationsAdaptive(
        limit: 10000
        filter: {
          datetime_geq: $datetimeStart
          datetime_lt: $datetimeEnd
          scriptName: $scriptName
        }
      ) { sum { requests } }
      d1: d1AnalyticsAdaptiveGroups(
        limit: 10000
        filter: {date: $date, databaseId: $databaseId}
      ) { sum { rowsRead rowsWritten } }
      r2: r2OperationsAdaptiveGroups(
        limit: 10000
        filter: {
          datetime_geq: $datetimeStart
          datetime_lt: $datetimeEnd
          bucketName: $bucketName
        }
      ) { sum { requests } dimensions { actionType } }
    }
  }
}
""".strip()


def collect_daily_usage(
    *,
    workflow_run_id: int,
    day: date,
    cloudflare_token: str,
    captured_at: datetime,
    github_token: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    if isinstance(workflow_run_id, bool) or not isinstance(workflow_run_id, int) or workflow_run_id < 1:
        raise UsageCollectionError("workflow_run_id must be a positive integer")
    cf_token = _secret(cloudflare_token, "CF_ANALYTICS_API_TOKEN")
    window_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    window_end = window_start + timedelta(days=1)
    if captured_at < window_end:
        raise UsageCollectionError("usage collection must run after the UTC day has ended")

    owned_client = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=False)
    try:
        github_headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "finance-crawler-validation-soak-collector/1",
        }
        if github_token is not None and github_token.strip():
            github_headers["Authorization"] = f"Bearer {github_token.strip()}"
        github_base = f"{GITHUB_API}/repos/{GITHUB_REPOSITORY}"
        repository = _get_json(http, github_base, github_headers, "GitHub repository")
        run = _get_json(
            http,
            f"{github_base}/actions/runs/{workflow_run_id}",
            github_headers,
            "GitHub workflow run",
        )
        run_mapping = _mapping(run, "GitHub workflow run")
        attempt = run_mapping.get("run_attempt")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise UsageCollectionError("GitHub run_attempt must be positive")
        jobs = _get_json(
            http,
            f"{github_base}/actions/runs/{workflow_run_id}/attempts/{attempt}/jobs",
            github_headers,
            "GitHub workflow jobs",
            params={"per_page": "100"},
        )
        graphql = _post_json(
            http,
            CLOUDFLARE_GRAPHQL,
            {"Authorization": f"Bearer {cf_token}"},
            {
                "query": CLOUDFLARE_QUERY,
                "variables": {
                    "accountTag": CLOUDFLARE_ACCOUNT_ID,
                    "bucketName": R2_BUCKET,
                    "databaseId": D1_DATABASE_ID,
                    "date": day.isoformat(),
                    "datetimeEnd": _utc_text(window_end),
                    "datetimeStart": _utc_text(window_start),
                    "scriptName": WORKER_SCRIPT,
                },
            },
            "Cloudflare GraphQL",
        )
    finally:
        if owned_client:
            http.close()

    account = _cloudflare_account(graphql)
    return build_daily_usage(
        github_run=run,
        github_repository=repository,
        github_jobs=jobs,
        worker_groups=account.get("workers"),
        d1_groups=account.get("d1"),
        r2_groups=account.get("r2"),
        window_start=window_start,
        window_end=window_end,
        captured_at=captured_at,
        cloudflare_account_id=CLOUDFLARE_ACCOUNT_ID,
        worker_script=WORKER_SCRIPT,
        d1_database_id=D1_DATABASE_ID,
        r2_bucket=R2_BUCKET,
    )


def write_private_json(path: Path, payload: object) -> None:
    parent_existed = path.parent.exists()
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not parent_existed:
            path.parent.chmod(0o700)
        elif path.parent.stat().st_mode & 0o077:
            raise UsageCollectionError(
                "private output directory must not grant group or other permissions"
            )
    except OSError as exc:
        raise UsageCollectionError(f"cannot secure private output directory: {exc}") from exc
    if path.exists() or path.is_symlink():
        raise UsageCollectionError("private usage evidence already exists")
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise UsageCollectionError("private output temporary path already exists")
    try:
        content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise UsageCollectionError("private usage evidence already exists") from exc
        temporary.unlink()
    except UsageCollectionError:
        raise
    except OSError as exc:
        raise UsageCollectionError(f"cannot write private usage evidence: {exc}") from exc


def _get_json(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    name: str,
    params: Mapping[str, str] | None = None,
) -> object:
    try:
        response = client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UsageCollectionError(f"{name} request failed") from exc


def _post_json(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    body: object,
    name: str,
) -> object:
    try:
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UsageCollectionError(f"{name} request failed") from exc


def _cloudflare_account(value: object) -> Mapping[str, object]:
    response = _mapping(value, "Cloudflare GraphQL response")
    errors = response.get("errors")
    if errors not in (None, []):
        raise UsageCollectionError("Cloudflare GraphQL errors were returned")
    data = _mapping(response.get("data"), "Cloudflare GraphQL data")
    viewer = _mapping(data.get("viewer"), "Cloudflare GraphQL viewer")
    accounts = viewer.get("accounts")
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise UsageCollectionError("Cloudflare GraphQL must return exactly one Cloudflare account")
    return _mapping(accounts[0], "Cloudflare GraphQL account")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise UsageCollectionError(f"{name} must be an object")
    return value


def _secret(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsageCollectionError(f"{name} is required")
    return value.strip()


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect private P0 daily usage evidence")
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = collect_daily_usage(
            workflow_run_id=args.workflow_run_id,
            day=args.day,
            cloudflare_token=os.environ.get("CF_ANALYTICS_API_TOKEN", ""),
            github_token=os.environ.get("GITHUB_TOKEN"),
            captured_at=datetime.now(timezone.utc),
        )
        write_private_json(args.output, result)
    except UsageCollectionError as exc:
        print(json.dumps({"accepted": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"accepted": True, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
