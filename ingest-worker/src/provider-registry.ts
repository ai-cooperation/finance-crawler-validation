import registryJson from "./generated/provider-registry.json";


export interface ProviderMetricSupport {
  metric_id: string;
  support_level: "exact" | "derived" | "proxy";
  notes?: string;
}

export interface ProviderConnection {
  provider_id: string;
  catalog_status: string;
  execution_policy: string;
  data_plane_state: string;
  adapter: string;
  runtime: string;
  transports: string[];
  required_configuration: string[];
  next_action: string;
  endpoint_template?: string;
  auth_injection?: string;
  auth_field?: string;
  last_verified_at?: string;
  probe?: Record<string, unknown>;
}

export interface RuntimeProvider {
  provider_id: string;
  name: string;
  provider_type: string;
  source_tier: string;
  homepage_url: string;
  documentation_url: string;
  categories: string[];
  requirement_ids: string[];
  metric_support: ProviderMetricSupport[];
  geographies: string[];
  asset_classes: string[];
  languages: string[];
  access: { transports: string[]; auth: string; cost_tier: string };
  rights: { public_raw_storage: string };
  integration: { status: string; callable: boolean };
  connection: ProviderConnection;
}

export interface ProviderFilters {
  q?: string;
  geography?: string;
  metric?: string;
  status?: string;
  execution?: string;
  callable?: boolean;
  limit?: number;
  offset?: number;
}

export class ProviderQueryError extends Error {
  constructor(public readonly code: string) {
    super(code);
  }
}

interface RuntimeRegistry {
  schema_version: number;
  registry_id: string;
  generated_at: string;
  catalog_id: string;
  summary: {
    total: number;
    route_integrated: number;
    activation_backlog: number;
    technically_connectable_backlog: number;
    not_executable: number;
  };
  providers: RuntimeProvider[];
}

const REGISTRY = registryJson as RuntimeRegistry;

export const PROVIDER_MCP_TOOLS = [
  {
    name: "list_data_providers",
    description: "Find investment-research providers by geography, metric, integration status or execution state. Results distinguish route-integrated sources from probe-only and policy-gated candidates.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        q: { type: "string" },
        geography: { type: "string" },
        metric: { type: "string" },
        status: { type: "string" },
        execution: { type: "string" },
        callable: { type: "boolean" },
        limit: { type: "integer", minimum: 1, maximum: 100 },
        offset: { type: "integer", minimum: 0 },
      },
    },
  },
  {
    name: "get_data_provider",
    description: "Read one provider connector contract, including runtime, adapter, rights boundary, required configuration and exact next action.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["provider_id"],
      properties: {
        provider_id: { type: "string", pattern: "^[a-z][a-z0-9_-]{1,127}$" },
      },
    },
  },
] as const;

export function providerRegistrySummary(): RuntimeRegistry["summary"] {
  return { ...REGISTRY.summary };
}

export function getProvider(providerId: string): RuntimeProvider | null {
  const normalized = providerId.trim().toLocaleLowerCase();
  return REGISTRY.providers.find((provider) => provider.provider_id === normalized) ?? null;
}

export function listProviders(filters: ProviderFilters = {}): {
  schema_version: 1;
  registry_id: string;
  generated_at: string;
  summary: RuntimeRegistry["summary"];
  total_matches: number;
  count: number;
  next_offset: number | null;
  providers: RuntimeProvider[];
} {
  const limit = Math.min(100, Math.max(1, Math.trunc(filters.limit ?? 50)));
  const offset = Math.max(0, Math.trunc(filters.offset ?? 0));
  const text = normalized(filters.q);
  const geography = normalized(filters.geography);
  const metric = normalized(filters.metric);
  const status = normalized(filters.status);
  const execution = normalized(filters.execution);
  const matched = REGISTRY.providers.filter((provider) => {
    if (text) {
      const haystack = [
        provider.provider_id,
        provider.name,
        ...provider.categories,
        ...provider.requirement_ids,
      ].join(" ").toLocaleLowerCase();
      if (!haystack.includes(text)) return false;
    }
    if (geography && !provider.geographies.some((value) => normalized(value) === geography)) {
      return false;
    }
    if (metric && !provider.metric_support.some((value) => normalized(value.metric_id) === metric)) {
      return false;
    }
    if (status && normalized(provider.integration.status) !== status) return false;
    if (execution && normalized(provider.connection.data_plane_state) !== execution) return false;
    if (filters.callable !== undefined && provider.integration.callable !== filters.callable) return false;
    return true;
  });
  const providers = matched.slice(offset, offset + limit);
  const nextOffset = offset + providers.length < matched.length ? offset + providers.length : null;
  return {
    schema_version: 1,
    registry_id: REGISTRY.registry_id,
    generated_at: REGISTRY.generated_at,
    summary: providerRegistrySummary(),
    total_matches: matched.length,
    count: providers.length,
    next_offset: nextOffset,
    providers,
  };
}

export function parseProviderFilters(params: URLSearchParams): ProviderFilters {
  const allowed = new Set(["q", "geography", "metric", "status", "execution", "callable", "limit", "offset"]);
  for (const key of params.keys()) {
    if (!allowed.has(key)) throw new ProviderQueryError("query_parameter_unknown");
  }
  const result: ProviderFilters = {};
  for (const key of ["q", "geography", "metric", "status", "execution"] as const) {
    const value = params.get(key);
    if (value !== null) {
      const trimmed = value.trim();
      if (!trimmed || trimmed.length > 128) throw new ProviderQueryError(`${key}_invalid`);
      result[key] = trimmed;
    }
  }
  const callable = params.get("callable");
  if (callable !== null) {
    if (callable !== "true" && callable !== "false") throw new ProviderQueryError("callable_invalid");
    result.callable = callable === "true";
  }
  const limit = params.get("limit");
  if (limit !== null) result.limit = boundedInteger(limit, "limit", 1, 100);
  const offset = params.get("offset");
  if (offset !== null) result.offset = boundedInteger(offset, "offset", 0, 10_000);
  return result;
}

export function parseProviderFilterObject(value: unknown): ProviderFilters {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ProviderQueryError("provider_filters_invalid");
  }
  const input = value as Record<string, unknown>;
  const params = new URLSearchParams();
  for (const [key, candidate] of Object.entries(input)) {
    if (typeof candidate === "string" || typeof candidate === "number" || typeof candidate === "boolean") {
      params.set(key, String(candidate));
      continue;
    }
    throw new ProviderQueryError(`${key}_invalid`);
  }
  return parseProviderFilters(params);
}

function normalized(value: string | undefined): string {
  return value?.trim().toLocaleLowerCase() ?? "";
}

function boundedInteger(value: string, field: string, minimum: number, maximum: number): number {
  if (!/^\d+$/.test(value)) throw new ProviderQueryError(`${field}_invalid`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new ProviderQueryError(`${field}_invalid`);
  }
  return parsed;
}
