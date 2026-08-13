import { describe, expect, it, vi } from "vitest";

import {
  AlertDeliveryError,
  deliverAlertWebhook,
  readAlertWebhookFormat,
} from "../src/alerts";


const FRESHNESS_PAYLOAD = {
  alert_key: "topic_radar_freshness",
  state: "open",
  severity: "critical",
  summary: "Finance topic radar snapshot is stale",
  details: {},
};

describe("alert provider adapters", () => {
  it("auto-detects Slack and sends its documented text payload", async () => {
    let delivery: { input: string; init: RequestInit } | null = null;
    const webhookUrl = "https://hooks.slack.com/services/T00000000/B00000000/synthetic-token";
    await deliverAlertWebhook(
      webhookUrl,
      "github_action_failure:31309377786",
      {
        alert_key: "github_action_failure:31309377786",
        state: "open",
        severity: "critical",
        summary: "Finance topic radar failed",
        details: {
          run_url: "https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31309377786",
          private_evidence: "must-not-leak",
        },
      },
      async (input, init) => {
        delivery = { input: String(input), init: init ?? {} };
        return new Response("ok", { status: 200 });
      },
      "auto",
    );

    expect(delivery?.input).toBe(webhookUrl);
    const body = JSON.parse(String(delivery?.init.body));
    expect(body).toEqual({
      text: "🚨 Finance topic radar failed\ngithub_action_failure:31309377786\nhttps://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31309377786",
    });
    expect(String(delivery?.init.body)).not.toContain("private_evidence");
    expect(String(delivery?.init.body)).not.toContain("must-not-leak");
  });

  it("auto-detects Telegram, moves chat_id into JSON, and verifies ok", async () => {
    let delivery: { input: string; init: RequestInit } | null = null;
    const webhookUrl = "https://api.telegram.org/bot123456:synthetic-token/sendMessage?chat_id=-100123456";
    await deliverAlertWebhook(
      webhookUrl,
      "topic_radar_freshness:open:2026-08-13T08:00:00Z",
      { ...FRESHNESS_PAYLOAD, details: { private_evidence: "must-not-leak" } },
      async (input, init) => {
        delivery = { input: String(input), init: init ?? {} };
        return Response.json({ ok: true, result: { message_id: 1 } });
      },
      "auto",
    );

    expect(delivery?.input).toBe(
      "https://api.telegram.org/bot123456:synthetic-token/sendMessage",
    );
    const body = JSON.parse(String(delivery?.init.body));
    expect(body).toEqual({
      chat_id: "-100123456",
      text: "🚨 Finance topic radar snapshot is stale\ntopic_radar_freshness",
      link_preview_options: { is_disabled: true },
    });
    expect(String(delivery?.init.body)).not.toContain("private_evidence");
  });

  it("auto-detects ntfy and preserves generic JSON for other HTTPS sinks", async () => {
    const deliveries: Array<{ input: string; body: Record<string, unknown> }> = [];
    const capture = async (input: RequestInfo | URL, init?: RequestInit) => {
      deliveries.push({
        input: String(input),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      return new Response("ok", { status: 200 });
    };

    await deliverAlertWebhook(
      "https://ntfy.sh/finance-radar-synthetic-topic",
      "topic_radar_freshness",
      FRESHNESS_PAYLOAD,
      capture,
      "auto",
    );
    await deliverAlertWebhook(
      "https://alerts.example/hooks/finance-radar",
      "topic_radar_freshness",
      FRESHNESS_PAYLOAD,
      capture,
      "auto",
    );

    expect(deliveries[0]).toMatchObject({
      input: "https://ntfy.sh/",
      body: {
        topic: "finance-radar-synthetic-topic",
        title: "Finance crawler alert",
      },
    });
    expect(deliveries[1]).toEqual({
      input: "https://alerts.example/hooks/finance-radar",
      body: FRESHNESS_PAYLOAD,
    });
  });

  it("rejects deceptive provider URLs and provider-level failures", async () => {
    await expect(deliverAlertWebhook(
      "https://hooks.slack.com.evil.example/services/a/b/c",
      "topic_radar_freshness",
      FRESHNESS_PAYLOAD,
      async () => new Response("ok", { status: 200 }),
      "auto",
    )).rejects.toMatchObject({ code: "alert_webhook_invalid" });
    await expect(deliverAlertWebhook(
      "https://ntfy.sh.evil.example/synthetic-topic-1234",
      "topic_radar_freshness",
      FRESHNESS_PAYLOAD,
      async () => new Response("ok", { status: 200 }),
      "auto",
    )).rejects.toMatchObject({ code: "alert_webhook_invalid" });
    await expect(deliverAlertWebhook(
      "https://hooks.slack.com/services/a/b/c",
      "topic_radar_freshness",
      FRESHNESS_PAYLOAD,
      async () => new Response("invalid_payload", { status: 200 }),
      "auto",
    )).rejects.toMatchObject({ code: "alert_delivery_rejected" });
    await expect(deliverAlertWebhook(
      "https://api.telegram.org/bot123:synthetic/sendMessage?chat_id=1&extra=1",
      "topic_radar_freshness",
      FRESHNESS_PAYLOAD,
      async () => Response.json({ ok: true }),
      "auto",
    )).rejects.toBeInstanceOf(AlertDeliveryError);
    await expect(deliverAlertWebhook(
      "https://api.telegram.org/bot123:synthetic/sendMessage?chat_id=1",
      "topic_radar_freshness",
      FRESHNESS_PAYLOAD,
      async () => Response.json({ ok: false, description: "synthetic rejection" }),
      "auto",
    )).rejects.toMatchObject({ code: "alert_delivery_rejected" });
  });

  it("accepts auto as the configured webhook format", () => {
    expect(readAlertWebhookFormat({ ALERT_WEBHOOK_FORMAT: "auto" } as Env)).toBe("auto");
  });

  it("sets a bounded timeout on every outbound alert request", async () => {
    let signal: AbortSignal | null = null;
    await deliverAlertWebhook(
      "https://alerts.example/hooks/finance-radar",
      "topic_radar_freshness",
      FRESHNESS_PAYLOAD,
      async (_input, init) => {
        signal = init?.signal as AbortSignal;
        return new Response("ok", { status: 200 });
      },
      "generic_json",
    );

    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal?.aborted).toBe(false);
  });

  it("never logs a webhook URL or provider token on network failure", async () => {
    const webhookUrl = "https://api.telegram.org/bot123456:synthetic-secret/sendMessage?chat_id=1";
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      await expect(deliverAlertWebhook(
        webhookUrl,
        "topic_radar_freshness",
        FRESHNESS_PAYLOAD,
        async () => {
          throw new Error(`synthetic fetch failure for ${webhookUrl}`);
        },
        "auto",
      )).rejects.toMatchObject({ code: "alert_delivery_failed" });
      const serializedLogs = JSON.stringify(errorLog.mock.calls);
      expect(serializedLogs).not.toContain(webhookUrl);
      expect(serializedLogs).not.toContain("synthetic-secret");
      expect(serializedLogs).toContain("alert_delivery_network_error");
    } finally {
      errorLog.mockRestore();
    }
  });
});
