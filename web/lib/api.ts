// Chooses the data source. Fixtures are the default only until the API (#16) exists; set
// NEXT_PUBLIC_RELAY_API_URL to use the real backend. The shell shows a banner in fixtures mode.
import { createFixtureApi } from "@/lib/fixtures";
import type {
  ConnectorHealth,
  FaultMode,
  FaultRule,
  HumanTask,
  KillScope,
  KillSwitch,
  MetricsSummary,
  PolicyPack,
  PolicyPackSummary,
  PolicyTestResult,
  RelayApi,
  RunDetail,
  RunSummary,
  Session,
  User,
} from "@/lib/types";

const TOKEN_KEY = "relay.session";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export function storedSession(): Session | null {
  try {
    const raw = window.localStorage.getItem(TOKEN_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function storeSession(session: Session | null): void {
  try {
    if (session) window.localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Without storage the session lasts only as long as the page.
  }
}

function createHttpApi(baseUrl: string): RelayApi {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = storedSession()?.token;
    let response: Response;
    try {
      response = await fetch(`${baseUrl}/api/v1${path}`, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init?.headers,
        },
      });
    } catch {
      throw new ApiError("The Relay API cannot be reached.", 0);
    }
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
      const detail = typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`;
      throw new ApiError(detail, response.status);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  const post = <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

  return {
    isDemoData: false,
    demoUsers: () => request<User[]>("/auth/demo-users"),
    login: (email) => post<Session>("/auth/demo-login", { email }),
    metrics: (workflow) =>
      request<MetricsSummary>(
        `/metrics/summary${workflow ? `?workflow=${encodeURIComponent(workflow)}` : ""}`,
      ),
    runs: (status) => request<RunSummary[]>(`/runs${status ? `?status=${status}` : ""}`),
    run: (id) => request<RunDetail>(`/runs/${encodeURIComponent(id)}`),
    tasks: () => request<HumanTask[]>("/tasks?mine=true"),
    decideTask: (id, option, reason) =>
      post<HumanTask>(`/tasks/${encodeURIComponent(id)}/decide`, { option, reason }),
    policies: () => request<PolicyPackSummary[]>("/policies"),
    policy: (pack) => request<PolicyPack>(`/policies/${encodeURIComponent(pack)}`),
    saveDraft: (pack, content) =>
      request<void>(`/policies/${encodeURIComponent(pack)}/draft`, {
        method: "PUT",
        body: JSON.stringify({ content }),
      }),
    testPolicy: (pack) => post<PolicyTestResult[]>(`/policies/${encodeURIComponent(pack)}/test`),
    activatePolicy: (pack) =>
      post<PolicyPackSummary>(`/policies/${encodeURIComponent(pack)}/activate`),
    killSwitches: () => request<KillSwitch[]>("/kill-switches"),
    setKillSwitch: (scope: KillScope, target: string, active: boolean, note?: string) =>
      post<KillSwitch>("/kill-switches", { scope, target, active, note }),
    connectorHealth: () => request<ConnectorHealth[]>("/connectors/health"),
    faults: () => request<FaultRule[]>("/controls/faults"),
    addFault: (system: string, fault: FaultMode) =>
      post<FaultRule>("/controls/faults", { system, fault }),
    clearFaults: () => request<void>("/controls/faults", { method: "DELETE" }),
  };
}

const baseUrl = process.env.NEXT_PUBLIC_RELAY_API_URL;

export const api: RelayApi = baseUrl ? createHttpApi(baseUrl.replace(/\/$/, "")) : createFixtureApi();

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
