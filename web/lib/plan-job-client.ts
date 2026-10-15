import type { PublicSnapshot } from "./types";

export type PlanProgress = {
  id: string; status: "RUNNING" | "SUCCEEDED" | "FAILED";
  stage: string; detail: string; elapsed_seconds: number; error: string | null;
};
type Options = {
  baseUrl: string; token: string; input: Record<string, unknown>;
  onProgress?: (progress: PlanProgress) => void;
  fetcher?: typeof fetch; pause?: (milliseconds: number) => Promise<void>;
  now?: () => number;
};

/** Short authenticated requests; never retry by starting a different scan. */
export async function runPlanJob({ baseUrl, token, input, onProgress, fetcher = fetch,
  pause = ms => new Promise(resolve => setTimeout(resolve, ms)), now = Date.now }: Options): Promise<PublicSnapshot> {
  const deadline = now() + 13 * 60_000;
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  async function request(path: string, body?: Record<string, unknown>) {
    const response = await fetcher(`${baseUrl}${path}`, {
      method: body ? "POST" : "GET", headers, ...(body ? { body: JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(50_000),
    });
    let value;
    try { value = await response.json(); } catch { value = null; }
    if (!response.ok) {
      const message = response.status === 401 ? "Your session expired. Sign in again; refresh saved plans before retrying."
        : response.status === 404 ? "Scan progress is unavailable or the service restarted. Refresh saved plans before retrying."
        : typeof value?.detail === "string" ? value.detail.slice(0, 600)
        : "The planning service did not confirm a result. Refresh saved plans before retrying.";
      throw Object.assign(new Error(message), { status: response.status });
    }
    if (!value || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.id) || !["RUNNING", "SUCCEEDED", "FAILED"].includes(value.status)
      || typeof value.stage !== "string" || typeof value.detail !== "string"
      || !Number.isFinite(value.elapsed_seconds) || value.elapsed_seconds < 0) {
      throw Object.assign(new Error("The planning service returned invalid progress. Refresh saved plans before retrying."), { status: 502 });
    }
    return value as PlanProgress & { snapshot?: PublicSnapshot };
  }
  // One lost-start retry retains the exact request ID; no automatic new scan.
  let progress;
  try { progress = await request("/v1/plan-jobs", input); }
  catch (error) {
    const status = (error as { status?: number }).status;
    if (status !== undefined && ![502, 504].includes(status)) throw error;
    await pause(2000);
    progress = await request("/v1/plan-jobs", input);
  }
  // A retried start may find a job that has already saved its plan. Only the
  // owner-authenticated status read reloads the final account snapshot.
  if (progress.status === "SUCCEEDED" && !progress.snapshot) {
    progress = await request(`/v1/plan-jobs/${progress.id}`);
  }
  let transportFailures = 0;
  while (true) {
    onProgress?.(progress);
    if (progress.status === "FAILED") throw new Error(progress.error || "The scan stopped without a confirmed result. Refresh saved plans before retrying.");
    if (progress.status === "SUCCEEDED") {
      if (!progress.snapshot?.account || !progress.snapshot?.plan || !Array.isArray(progress.snapshot.recommendations)) {
        throw new Error("The saved plan could not be reloaded. Refresh your account; do not start another scan yet.");
      }
      return progress.snapshot;
    }
    if (now() >= deadline) throw new Error("Stopped waiting for scan progress. It may still be running; refresh saved plans before retrying.");
    await pause(3000);
    try {
      progress = await request(`/v1/plan-jobs/${progress.id}`);
      transportFailures = 0;
    } catch (error) {
      const status = (error as { status?: number }).status;
      if (++transportFailures >= 3 || status !== undefined && ![502, 503, 504].includes(status)) throw error;
    }
  }
}
