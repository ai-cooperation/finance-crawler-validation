import { describe, expect, it } from "vitest";

import {
  getProvider,
  listProviders,
  parseProviderFilterObject,
  parseProviderFilters,
  PROVIDER_MCP_TOOLS,
  ProviderQueryError,
  providerRegistrySummary,
} from "../src/provider-registry";


describe("deployed provider registry", () => {
  it("lists all providers and exposes the route/backlog boundary", () => {
    expect(providerRegistrySummary()).toEqual({
      total: 110,
      route_integrated: 50,
      activation_backlog: 60,
      technically_connectable_backlog: 51,
      not_executable: 9,
    });

    expect(listProviders({ limit: 5 })).toMatchObject({
      schema_version: 1,
      count: 5,
      next_offset: 5,
      summary: { total: 110, route_integrated: 50 },
    });

    expect(listProviders({ q: "sec", status: "verified_requires_key", callable: true, limit: 100 })
      .providers.some((provider) => provider.provider_id === "sec_edgar")).toBe(true);
    expect(listProviders({ callable: false, offset: 10_000 }).providers).toEqual([]);
  });

  it("filters by geography, metric and execution state", () => {
    const body = listProviders({
      geography: "TW",
      metric: "revenue_growth",
      execution: "route_integrated",
      limit: 100,
    });

    expect(body.providers.length).toBeGreaterThan(0);
    expect(body.providers.every((provider) =>
      provider.geographies.includes("TW") &&
      provider.metric_support.some((metric) => metric.metric_id === "revenue_growth") &&
      provider.connection.data_plane_state === "route_integrated"
    )).toBe(true);
  });

  it("returns one provider and rejects an unknown provider", () => {
    expect(getProvider("  SEC_EDGAR  ")).toMatchObject({
      provider_id: "sec_edgar",
      integration: { status: "verified_requires_key", callable: true },
      connection: { data_plane_state: "route_integrated" },
    });
    expect(getProvider("not_a_provider")).toBeNull();
  });

  it("publishes provider discovery tools for MCP", () => {
    expect(PROVIDER_MCP_TOOLS.map((tool) => tool.name)).toEqual([
      "list_data_providers",
      "get_data_provider",
    ]);
  });

  it("validates public query parameters at the boundary", () => {
    expect(parseProviderFilters(new URLSearchParams("callable=true&limit=20&offset=2"))).toEqual({
      callable: true,
      limit: 20,
      offset: 2,
    });
    expect(() => parseProviderFilters(new URLSearchParams("callable=maybe"))).toThrow(
      ProviderQueryError,
    );
    expect(() => parseProviderFilters(new URLSearchParams("limit=1000"))).toThrow(
      "limit_invalid",
    );
    expect(parseProviderFilters(new URLSearchParams("q= SEC &geography=TW&metric=revenue_growth&status=verified_public&execution=route_integrated"))).toMatchObject({
      q: "SEC",
      geography: "TW",
      metric: "revenue_growth",
      status: "verified_public",
      execution: "route_integrated",
    });
    expect(() => parseProviderFilters(new URLSearchParams("unknown=value"))).toThrow(
      "query_parameter_unknown",
    );
    expect(() => parseProviderFilters(new URLSearchParams("q=   "))).toThrow("q_invalid");
    expect(() => parseProviderFilters(new URLSearchParams("offset=-1"))).toThrow("offset_invalid");
  });

  it("validates MCP filter objects before converting them to query parameters", () => {
    expect(parseProviderFilterObject({ q: "taiwan", callable: false, limit: 3 })).toEqual({
      q: "taiwan",
      callable: false,
      limit: 3,
    });
    expect(() => parseProviderFilterObject(null)).toThrow("provider_filters_invalid");
    expect(() => parseProviderFilterObject([])).toThrow("provider_filters_invalid");
    expect(() => parseProviderFilterObject({ q: ["taiwan"] })).toThrow("q_invalid");
  });
});
