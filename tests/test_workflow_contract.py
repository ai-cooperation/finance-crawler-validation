from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_workflow_can_select_the_bounded_foreign_community_manifest() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/crawl-capability.yml").read_text(
        encoding="utf-8"
    )

    assert "foreign_communities" in workflow
    assert "news_120" in workflow
    assert "foreign-community-sources.yaml" in workflow
    assert "news-sources.yaml" in workflow
    assert "resource-executors.yaml" in workflow
    assert "github_actions_crawl4ai" in workflow
    assert "finance-crawler-capability-report-${{ inputs.scope }}" in workflow
    assert "default: \"1\"" in workflow
    assert "CF_RELAY_BASE_URL: ${{ vars.CF_RELAY_BASE_URL }}" in workflow
    assert workflow.count("CF_RELAY_BASE_URL: ${{ vars.CF_RELAY_BASE_URL }}") == 2
    assert "node --test worker/test/index.test.mjs" in workflow
    assert "npm ci --prefix experiments/crawlee-browser" in workflow
    assert "node --test experiments/crawlee-browser/test/*.test.mjs" in workflow
    assert "node --test experiments/cloudflare-browser-run/test/*.test.mjs" in workflow
    assert "experiments/crawlee-browser/scripts/run-crawlee.mjs" in workflow
    assert "artifacts/crawlee-browser.json" in workflow
    assert (
        "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38 # v6.5.0"
        in workflow
    )
    assert "brand_ids:" in workflow
    assert "max_brands:" in workflow
    assert '--brand-ids "${{ inputs.brand_ids }}"' in workflow
    assert '--max-brands "${{ inputs.max_brands }}"' in workflow


def test_topic_radar_workflow_has_a_narrow_oidc_ingest_boundary() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/topic-radar.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert 'cron: "17 3 * * *"' in workflow
    assert "id-token: write" in workflow
    assert "contents: read" in workflow
    assert "issues: write" in workflow
    assert "finance-topic-radar" in workflow
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in workflow
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in workflow
    assert "finance-crawler-validation-ingest" in workflow
    assert "${{ vars.INGEST_WORKER_URL }}" in workflow
    assert '"$INGEST_WORKER_URL/v1/ingest/items"' in workflow
    assert '"$INGEST_WORKER_URL/v1/ingest/publish"' in workflow
    assert "finance-topic-radar-${{ github.run_id }}" in workflow
    assert "verify_resilience" in workflow
    assert "verify_alert_delivery" in workflow
    assert "verify_freshness_watchdog" in workflow
    assert "verify_soak_boundary" in workflow
    assert "default: false" in workflow
    assert "Verify replay and last-good resilience" in workflow
    assert "jq -er '.admitted | type == \"boolean\"'" in workflow
    assert "jq -r '.admitted'" in workflow
    assert "jq -er '.admitted'" not in workflow
    assert '"$INGEST_WORKER_URL/v1/status"' in workflow
    assert 'replayed == true' in workflow
    assert 'invalid_payload' in workflow
    assert "gh issue create" in workflow
    assert "Inject alert delivery validation failure" in workflow
    assert "Run authenticated freshness watchdog" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert (
        workflow.index("Inject alert delivery validation failure")
        < workflow.index("Check out repository")
        < workflow.index("Request OIDC admission and persisted checkpoints")
    )
    assert (
        workflow.index("Run authenticated freshness watchdog")
        < workflow.index("Check out repository")
        < workflow.index("Request OIDC admission and persisted checkpoints")
    )
    assert '"$INGEST_WORKER_URL/v1/alerts/freshness-check"' in workflow
    assert "!inputs.verify_freshness_watchdog" in workflow
    assert "failure_issue_number" in workflow
    assert "steps.admission.outputs.admitted == 'true'" in workflow
    assert "Install admission client only" in workflow
    assert "--no-deps -e ." in workflow
    assert "Install full collector" in workflow
    assert "Record schedule-only soak observation" in workflow
    assert "github.event_name == 'schedule'" in workflow
    assert '"$INGEST_WORKER_URL/v1/soak/observe"' in workflow
    assert "Verify manual identity cannot write soak evidence" in workflow
    assert 'test "$response_status" = "403"' in workflow
    assert 'error == "schedule_identity_required"' in workflow
    assert '--argjson run_attempt "$GITHUB_RUN_ATTEMPT"' in workflow
    assert ".run_attempt == (env.GITHUB_RUN_ATTEMPT | tonumber)" in workflow
    assert "soak-observation-${{ github.run_id }}" not in workflow
    assert "artifacts/topic-radar/soak-observation.json" not in workflow
    assert "soak-observation-response.json" not in workflow
    assert (
        workflow.index("Record schedule-only soak observation")
        < workflow.index("Deliver external failure alert through OIDC")
    )
    assert (
        workflow.index("Verify manual identity cannot write soak evidence")
        < workflow.index("Check out repository")
    )


def test_ingest_worker_is_locked_to_the_validation_repository() -> None:
    config = (REPOSITORY_ROOT / "ingest-worker/wrangler.jsonc").read_text(
        encoding="utf-8"
    )

    assert '"name": "finance-crawler-validation-ingest"' in config
    assert '"GITHUB_REPOSITORY_ID": "1329574278"' in config
    assert '"GITHUB_OWNER_ID": "258149792"' in config
    assert (
        '"GITHUB_WORKFLOW_REF": "ai-cooperation/finance-crawler-validation/'
        '.github/workflows/topic-radar.yml@refs/heads/main"'
        in config
    )
    assert '"GITHUB_OIDC_AUDIENCE": "finance-crawler-validation-ingest"' in config
    assert '"ALERT_WEBHOOK_FORMAT": "auto"' in config
    assert '"traces": { "enabled": false }' in config
    assert '"crons": ["17 */6 * * *"]' in config
