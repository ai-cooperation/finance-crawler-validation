import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";


const GITHUB_ISSUER = "https://token.actions.githubusercontent.com";
const GITHUB_JWKS = createRemoteJWKSet(
  new URL("https://token.actions.githubusercontent.com/.well-known/jwks"),
);

export interface AuthContext {
  workflowRunId: string;
  commitSha: string;
  runAttempt?: number;
  // The production OIDC authenticator always sets this. Optional injected
  // authenticators fail closed on schedule-only routes when it is absent.
  eventName?: string;
}

export interface McpAuthContext {
  subject: string;
  scopes: string[];
}

export interface ExpectedGithubClaims {
  repositoryId: string;
  ownerId: string;
  workflowRef: string;
  ref: string;
  eventName: string;
}

export class AuthenticationError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string) {
    super(code);
    this.name = "AuthenticationError";
    this.status = status;
    this.code = code;
  }
}

/**
 * MCP is a separate client boundary from GitHub OIDC.  The shared token is
 * deliberately fail-closed: a deployment without MCP_API_TOKEN cannot expose
 * private R2/D1 artifacts.  OAuth/Access can replace this verifier later
 * without changing the tool contract.
 */
export async function authenticateMcp(
  request: Request,
  env: Env,
): Promise<McpAuthContext> {
  const configured = secretValue(env, "MCP_API_TOKEN");
  if (!configured) throw new AuthenticationError(503, "mcp_auth_not_configured");
  const authorization = request.headers.get("Authorization") ?? "";
  const match = /^Bearer ([^\s]+)$/.exec(authorization);
  if (!match || !(await constantTimeEqual(match[1], configured))) {
    throw new AuthenticationError(401, "invalid_mcp_token");
  }
  const scopes = (secretValue(env, "MCP_SCOPES") ?? "research:submit research:read")
    .split(/[ ,]+/)
    .map((scope) => scope.trim())
    .filter((scope) => scope.length > 0);
  return { subject: "mcp-token", scopes };
}

export async function authenticateGithubOidc(
  request: Request,
  env: Env,
): Promise<AuthContext> {
  if (isPlaceholder(env.GITHUB_REPOSITORY_ID) || isPlaceholder(env.GITHUB_OWNER_ID)) {
    throw new AuthenticationError(503, "oidc_not_configured");
  }
  const authorization = request.headers.get("Authorization") ?? "";
  const match = /^Bearer ([^\s]+)$/.exec(authorization);
  if (!match) throw new AuthenticationError(401, "missing_bearer_token");

  let payload: JWTPayload;
  try {
    ({ payload } = await jwtVerify(match[1], GITHUB_JWKS, {
      issuer: GITHUB_ISSUER,
      audience: env.GITHUB_OIDC_AUDIENCE,
    }));
  } catch {
    throw new AuthenticationError(401, "invalid_oidc_token");
  }
  assertGithubClaims(payload, {
    repositoryId: env.GITHUB_REPOSITORY_ID,
    ownerId: env.GITHUB_OWNER_ID,
    workflowRef: env.GITHUB_WORKFLOW_REF,
    ref: env.GITHUB_REF,
    eventName: env.GITHUB_EVENT_NAME,
  });
  const workflowRunId = stringClaim(payload, "run_id");
  const commitSha = stringClaim(payload, "sha");
  const runAttempt = positiveIntegerClaim(payload, "run_attempt");
  const eventName = stringClaim(payload, "event_name");
  if (!/^[a-f0-9]{40}$/.test(commitSha)) {
    throw new AuthenticationError(403, "oidc_claim_invalid:sha");
  }
  return { workflowRunId, commitSha, runAttempt, eventName };
}

function isPlaceholder(value: string): boolean {
  return value === "TBD";
}

function secretValue(env: Env, name: string): string | null {
  const value = Reflect.get(env, name);
  return typeof value === "string" && value.length > 0 ? value : null;
}

async function constantTimeEqual(left: string, right: string): Promise<boolean> {
  const [leftDigest, rightDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(left)),
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(right)),
  ]);
  const leftBytes = new Uint8Array(leftDigest);
  const rightBytes = new Uint8Array(rightDigest);
  let mismatch = leftBytes.length ^ rightBytes.length;
  for (let index = 0; index < Math.max(leftBytes.length, rightBytes.length); index += 1) {
    mismatch |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return mismatch === 0;
}

export function assertGithubClaims(
  claims: Record<string, unknown>,
  expected: ExpectedGithubClaims,
): void {
  const checks: ReadonlyArray<[string, string]> = [
    ["repository_id", expected.repositoryId],
    ["repository_owner_id", expected.ownerId],
    ["workflow_ref", expected.workflowRef],
    ["ref", expected.ref],
  ];
  for (const [claimName, expectedValue] of checks) {
    if (stringClaim(claims, claimName) !== expectedValue) {
      throw new AuthenticationError(403, `oidc_claim_mismatch:${claimName}`);
    }
  }
  const eventName = stringClaim(claims, "event_name");
  const allowedEvents = expected.eventName.split(",").map((value) => value.trim());
  if (!allowedEvents.includes(eventName)) {
    throw new AuthenticationError(403, "oidc_claim_mismatch:event_name");
  }
}

function stringClaim(claims: Record<string, unknown>, name: string): string {
  const value = claims[name];
  if (typeof value !== "string" || value.length === 0) {
    throw new AuthenticationError(403, `oidc_claim_missing:${name}`);
  }
  return value;
}

function positiveIntegerClaim(claims: Record<string, unknown>, name: string): number {
  const value = stringClaim(claims, name);
  if (!/^\d+$/.test(value) || Number(value) < 1 || !Number.isSafeInteger(Number(value))) {
    throw new AuthenticationError(403, `oidc_claim_invalid:${name}`);
  }
  return Number(value);
}
