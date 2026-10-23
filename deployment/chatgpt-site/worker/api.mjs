// Hosted adapter for the existing Supabase RPCs. Strategy computation remains
// in the original Python API; this module has no plan writer or broker tools.
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_BODY_BYTES = 128_000;
const MAX_RESPONSE_BYTES = 8_000_000;
const EXPORT_TABLES = {
  profiles: "user_id", competition_accounts: "id", positions: "id", plans: "id",
  recommendations: "id", recommendation_events: "id", paper_actions: "id",
  portfolio_snapshots: "id", import_jobs: "id", audit_events: "id", planning_profiles: "id",
};

function httpsOrigin(raw, label) {
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/"
        || url.search || url.hash || !/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(url.hostname)
        || /^(localhost|127\.|0\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/i.test(url.hostname)) throw new Error();
    return url.origin;
  } catch { throw new ApiError(503, `${label} is not configured with a valid HTTPS address.`); }
}

export function publicConfig(env) {
  let url = "";
  try { url = httpsOrigin(env.STOCK_COMP_SUPABASE_URL, "Account storage"); } catch {}
  const key = env.STOCK_COMP_SUPABASE_PUBLISHABLE_KEY || "";
  // Reject a server key rather than ever injecting it into a browser response.
  let publicKey = /^sb_publishable_[A-Za-z0-9_-]+$/.test(key) ? key : "";
  if (!publicKey && /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(key)) {
    try { if (JSON.parse(atob(key.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).role === "anon") publicKey = key; } catch {}
  }
  let compute = false;
  try { compute = Boolean(httpsOrigin(env.STOCK_COMP_PYTHON_API_URL, "Strategy service")); } catch {}
  return {
    supabase_url: url, supabase_publishable_key: publicKey,
    storage_configured: Boolean(url && publicKey), compute_configured: compute,
    compute_message: compute ? "" : "Daily scanning and automatic stop guidance are waiting for the strategy service setup. Saved accounts and history remain available.",
    demo_mode: false,
  };
}

async function checkedJson(response) {
  if (Number(response.headers.get("content-length")) > MAX_RESPONSE_BYTES) throw new ApiError(502, "Account response exceeds the supported size.");
  const reader = response.body?.getReader();
  const chunks = []; let length = 0;
  if (!reader) throw new ApiError(502, "Account storage returned an empty response.");
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > MAX_RESPONSE_BYTES) { await reader.cancel(); throw new ApiError(502, "Account response exceeds the supported size."); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); }
  catch { throw new ApiError(502, "The service returned an invalid response."); }
}

async function serviceJson(response) {
  try { return await checkedJson(response); }
  catch (error) {
    // A host may return plain-text or HTML for 401/429/503 responses (including
    // free-host startup pages). Retain its status without exposing that body.
    if (!response.ok && error instanceof ApiError && ["The service returned an invalid response.", "Account storage returned an empty response."].includes(error.message)) return {};
    throw error;
  }
}

async function input(request) {
  if (!request.headers.get("content-type")?.startsWith("application/json")) throw new ApiError(415, "Send a JSON request.");
  if (Number(request.headers.get("content-length")) > MAX_BODY_BYTES) throw new ApiError(413, "Request is too large.");
  const reader = request.body?.getReader(); let total = 0; const chunks = [];
  if (!reader) throw new ApiError(422, "A JSON body is required.");
  while (true) {
    const { done, value } = await reader.read(); if (done) break;
    total += value.length;
    if (total > MAX_BODY_BYTES) { await reader.cancel(); throw new ApiError(413, "Request is too large."); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  let body; try { body = JSON.parse(new TextDecoder().decode(bytes)); } catch { throw new ApiError(422, "The JSON body is invalid."); }
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new ApiError(422, "A JSON object is required.");
  return body;
}

function text(value, label, limit) {
  if (typeof value !== "string" || !value.trim() || value.length > limit) throw new ApiError(422, `${label} is invalid.`);
  return value.trim().replace(/\s+/g, " ");
}
function number(value, label, min = 0, max = Number.MAX_SAFE_INTEGER, integer = false) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max || (integer && !Number.isSafeInteger(value))) throw new ApiError(422, `${label} is invalid.`);
  return value;
}
function uuid(value) { if (!UUID.test(value || "")) throw new ApiError(422, "The record identifier is invalid."); return value; }
function date(value) {
  if (typeof value !== "string" || value.length > 40 || !/^\d{4}-\d{2}-\d{2}T/.test(value) || !Number.isFinite(Date.parse(value))) throw new ApiError(422, "A valid account timestamp is required.");
  return value;
}
function enumValue(value, choices, label) { if (!choices.includes(value)) throw new ApiError(422, `${label} is invalid.`); return value; }

export function visibleSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object" || !snapshot.account || !snapshot.portfolio || !snapshot.plan
      || !Array.isArray(snapshot.positions) || !Array.isArray(snapshot.recommendations)) throw new ApiError(502, "Account storage returned an invalid snapshot.");
  const result = structuredClone(snapshot);
  if (result.plan.status !== "CURRENT") {
    result.recommendations = [];
    result.regime = {
      name: "UNAVAILABLE", label: "Unavailable",
      reason: "The latest saved plan is not current for this portfolio. Generate a new plan to refresh its market regime.",
      trend_strength: null, market_breadth: null, volatility: null, preferred_strategies: [],
    };
  }
  return result;
}

function accountPayload(body, update) {
  const result = {
    p_name: text(body.name, "Account name", 120),
    p_starting_capital: number(body.starting_capital, "Starting capital"),
    p_cash: number(body.cash, "Cash"), p_snapshot_as_of: date(body.snapshot_as_of),
  };
  if (!update) return result;
  result.p_expected_portfolio_version = number(body.expected_portfolio_version, "Portfolio version", 1, Number.MAX_SAFE_INTEGER, true);
  if (!Array.isArray(body.positions) || body.positions.length > 200) throw new ApiError(422, "At most 200 holdings are supported.");
  const seen = new Set();
  result.p_positions = body.positions.map(p => {
    const ticker = text(p.ticker, "Ticker", 16).toUpperCase();
    if (!/^[A-Z0-9.\-]+$/.test(ticker)) throw new ApiError(422, "Ticker is invalid.");
    const side = enumValue(p.side, ["LONG", "SHORT"], "Holding side");
    const key = `${ticker}/${side}`; if (seen.has(key)) throw new ApiError(422, "Duplicate holdings are not allowed."); seen.add(key);
    const quantity = number(p.quantity, "Quantity", Number.MIN_VALUE);
    const average_price = p.average_price == null ? null : number(p.average_price, "Average price");
    const exact_cost_basis = p.exact_cost_basis == null ? null : number(p.exact_cost_basis, "Cost basis");
    if (average_price === null && exact_cost_basis === null) throw new ApiError(422, "Each holding needs an average price or exact cost basis.");
    return { ticker, side, quantity, average_price, exact_cost_basis };
  });
  return result;
}

function profilePayload(body, strategyNames) {
  const name = text(body.name, "Settings name", 80);
  const mode = enumValue(body.mode, ["COMPETITION", "TRADINGVIEW"], "Settings mode");
  const rules = body.rules;
  if (!rules || typeof rules !== "object" || Array.isArray(rules)) throw new ApiError(422, "Settings rules are invalid.");
  const r = mode === "COMPETITION" ? {
    strategy: enumValue(rules.strategy ?? "All strategies — consensus", strategyNames, "Strategy"),
    universe_mode: enumValue(rules.universe_mode ?? "EXPANDED", ["EXPANDED", "BROAD"], "Universe"),
    target_position_value: number(rules.target_position_value ?? 10000, "Position value", Number.MIN_VALUE, 20000),
    minimum_price_mode: enumValue(rules.minimum_price_mode ?? "ANY", ["ANY", "CUSTOM", "NO_MINIMUM"], "Price floor mode"),
    minimum_price: number(rules.minimum_price ?? 5, "Minimum price", 0, 1000000),
    stop_loss_enabled: rules.stop_loss_enabled ?? false,
    take_profit_enabled: rules.take_profit_enabled ?? false,
    app_settings: rules.app_settings ?? false,
  } : {
    max_position_value: number(rules.max_position_value ?? 20000, "Position value", Number.MIN_VALUE, 1000000),
    risk_per_trade_pct: number(rules.risk_per_trade_pct ?? 1, "Risk budget", Number.MIN_VALUE, 10),
    stop_loss_pct: number(rules.stop_loss_pct ?? 5, "Stop loss", Number.MIN_VALUE, 99.999999999),
    take_profit_pct: number(rules.take_profit_pct ?? 10, "Take profit", Number.MIN_VALUE, 500),
  };
  if (Object.keys(rules).some(key => !(key in r))) throw new ApiError(422, "Settings contain unknown rules.");
  for (const key of ["stop_loss_enabled", "take_profit_enabled", "app_settings"]) if (key in r && typeof r[key] !== "boolean") throw new ApiError(422, "Settings toggles must be boolean.");
  if (r.minimum_price_mode === "CUSTOM" && r.minimum_price <= 0) throw new ApiError(422, "Custom minimum price must be greater than zero.");
  return { name, mode, rules: r };
}

// Workers requires native fetch to retain its global receiver. Node's fetch
// permits a detached call, so injected-fetch unit tests alone cannot catch this.
export function createApi({ fetcher = (...args) => globalThis.fetch(...args), strategyNames = [], now = () => Date.now() } = {}) {
  const limits = new Map();
  return async function api(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/api/, "");
    if (path === "/v1/config" && request.method === "GET") return publicConfig(env);
    if (path === "/v1/health" && request.method === "GET") return { status: "ok", version: "sites-gateway-1", ...publicConfig(env), supabase_publishable_key: undefined, supabase_url: undefined };
    const config = publicConfig(env);
    if (!config.storage_configured) throw new ApiError(503, "Account storage is not configured.");
    const authorization = request.headers.get("authorization") || "";
    if (!/^Bearer \S{1,8192}$/i.test(authorization)) throw new ApiError(401, "Sign in to access your account.");
    async function upstream(target, options = {}, timeout = 10000) {
      let response;
      try {
        response = await fetcher(target, { ...options, redirect: "manual", signal: AbortSignal.timeout(timeout) });
        // Do not follow redirects with credentials. Manual mode is supported by
        // the hosted runtime; redirect:"error" is rejected before network I/O.
        if (response.status >= 300 && response.status < 400) throw new ApiError(502, "Account storage returned an unexpected redirect.");
        const value = await serviceJson(response);
        if (!response.ok) {
          if (response.status === 401 || response.status === 403) throw new ApiError(401, "Your session expired or access was denied. Please sign in again.");
          if (value?.code === "40001") throw new ApiError(409, "Your portfolio or plan changed. Refresh before trying again.");
          if (response.status === 404 || ["PGRST202", "42883"].includes(value?.code)) throw new ApiError(503, "Account storage needs the latest application migrations.");
          if (value?.code === "P0001") throw new ApiError(422, typeof value.message === "string" ? value.message.slice(0, 400) : "The account change was rejected.");
          if (response.status === 429) throw new ApiError(429, "The service is busy. Please wait and try again.");
          throw new ApiError(502, "Account storage returned an error.");
        }
        return value;
      } catch (error) {
        if (error instanceof ApiError) {
          if (error.status >= 500) console.error("account_storage_upstream", { path: new URL(target).pathname, status: response?.status ?? null, reason: error.message });
          throw error;
        }
        const message = error?.message || "";
        const category = /illegal invocation|incorrect this/i.test(message) ? "INVALID_FETCH_RECEIVER"
          : /AbortSignal|timeout.*function/i.test(message) ? "TIMEOUT_API_UNSUPPORTED"
          : /redirect/i.test(message) ? "REDIRECT_REJECTED"
          : /fetch.*function/i.test(message) ? "FETCH_API_UNSUPPORTED"
          : /network|connect|dns|resolve/i.test(message) ? "NETWORK_FAILURE"
          : error?.name || "Error";
        console.error("account_storage_upstream", { path: new URL(target).pathname, status: response?.status ?? null, reason: category });
        if (error?.name === "TimeoutError" || error?.name === "AbortError") throw new ApiError(504, "The service timed out. Refresh account state before retrying an action.");
        throw new ApiError(502, "The service is unavailable. Refresh account state before retrying an action.");
      }
    }
    const headers = { apikey: config.supabase_publishable_key, Authorization: authorization, "Content-Type": "application/json" };
    const user = await upstream(`${config.supabase_url}/auth/v1/user`, { headers });
    if (!UUID.test(user?.id || "")) throw new ApiError(401, "The session identity is invalid.");
    const write = !["GET", "HEAD"].includes(request.method);
    const rateKey = `${user.id}/${write ? "write" : "read"}`;
    const minute = Math.floor(now() / 60000);
    const bucket = limits.get(rateKey);
    const count = bucket?.minute === minute ? bucket.count + 1 : 1;
    if (limits.size > 1000) for (const [key, entry] of limits) if (entry.minute !== minute) limits.delete(key);
    limits.set(rateKey, { minute, count });
    if (count > (write ? 20 : 120)) throw new ApiError(429, "Too many requests. Please wait a minute.");
    async function rpc(name, body) { return upstream(`${config.supabase_url}/rest/v1/rpc/${name}`, { method: "POST", headers, body: JSON.stringify(body) }); }
    async function snapshot(id = null) {
      const result = await rpc("get_current_account_snapshot", { p_account_id: id });
      if (result === null) throw new ApiError(404, "Set up your competition account to continue.");
      return visibleSnapshot(result);
    }
    async function table(name, query = {}, options = {}) {
      const target = new URL(`${config.supabase_url}/rest/v1/${name}`);
      for (const [key, value] of Object.entries(query)) target.searchParams.set(key, String(value));
      return upstream(target, { ...options, headers: { ...headers, Prefer: "return=representation" } });
    }
    if (path === "/v1/snapshot" && request.method === "GET") return snapshot(url.searchParams.has("account_id") ? uuid(url.searchParams.get("account_id")) : null);
    if (path === "/v1/strategies" && request.method === "GET") return { strategies: strategyNames };
    if (path === "/v1/accounts" && request.method === "POST") return snapshot(uuid(await rpc("create_or_get_competition_account", accountPayload(await input(request), false))));
    if (path === "/v1/accounts/current" && request.method === "PUT") return snapshot(uuid(await rpc("update_competition_account_snapshot", accountPayload(await input(request), true))));
    if (path === "/v1/plans/history" && request.method === "GET") {
      const history = await rpc("get_account_plan_history", { p_limit: 20 });
      if (!Array.isArray(history)) throw new ApiError(502, "Plan history returned invalid data.");
      if (history.length) {
        const ids = history.map(item => uuid(item.id));
        const diagnostics = await table("plans", { select: "id,portfolio_snapshot", id: `in.(${ids.join(",")})` });
        if (!Array.isArray(diagnostics) || diagnostics.length !== ids.length || new Set(diagnostics.map(row => row.id)).size !== ids.length || diagnostics.some(row => !ids.includes(row.id))) throw new ApiError(502, "Plan diagnostics are incomplete.");
        for (const item of history) item.candidate_diagnostics = diagnostics.find(row => row.id === item.id)?.portfolio_snapshot?.candidate_diagnostics ?? {};
      }
      return history;
    }
    if (path === "/v1/paper-actions" && request.method === "GET") {
      const rows = await rpc("get_recent_paper_actions", { p_limit: 50 });
      if (!Array.isArray(rows)) throw new ApiError(502, "Paper action history returned invalid data.");
      return rows;
    }
    if (path === "/v1/paper-actions" && request.method === "POST") {
      const body = await input(request);
      if (typeof body.idempotency_key !== "string" || body.idempotency_key.length < 8 || body.idempotency_key.length > 200) throw new ApiError(422, "Action retry identifier must contain 8–200 characters.");
      const action = await rpc("record_recommendation_paper_action", {
        p_recommendation_id: uuid(body.recommendation_id),
        p_quantity: number(body.quantity, "Whole-share quantity", 1, 1000000, true),
        p_expected_portfolio_version: number(body.expected_portfolio_version, "Portfolio version", 1, Number.MAX_SAFE_INTEGER, true),
        p_idempotency_key: body.idempotency_key,
      });
      return { action_id: uuid(action), snapshot: await snapshot() };
    }
    const event = path.match(/^\/v1\/recommendations\/([^/]+)\/events$/);
    if (event && request.method === "POST") {
      const body = await input(request);
      await rpc("set_recommendation_status", { p_recommendation_id: uuid(event[1]), p_event_type: enumValue(body.event_type, ["DISMISSED", "REOPENED"], "Recommendation action") });
      return snapshot();
    }
    if (path === "/v1/planning-profiles" && request.method === "GET") {
      const rows = await table("planning_profiles", { select: "id,name,mode,rules,created_at,updated_at", order: "created_at.asc,id.asc" });
      if (!Array.isArray(rows)) throw new ApiError(502, "Settings returned invalid data.");
      return rows;
    }
    const profile = path.match(/^\/v1\/planning-profiles(?:\/([^/]+))?$/);
    if (profile && ((request.method === "POST" && !profile[1]) || (request.method === "PUT" && profile[1]))) {
      const body = await input(request);
      const payload = profilePayload(body, strategyNames);
      if (request.method === "POST") payload.user_id = user.id;
      const rows = await table("planning_profiles", profile[1] ? { id: `eq.${uuid(profile[1])}`, updated_at: `eq.${date(body.expected_updated_at)}` } : {}, {
        method: profile[1] ? "PATCH" : "POST", body: JSON.stringify(payload),
      });
      if (Array.isArray(rows) && rows.length === 0) throw new ApiError(409, "Settings changed elsewhere. Refresh before saving.");
      if (!Array.isArray(rows) || rows.length !== 1) throw new ApiError(502, "Settings returned invalid data.");
      return rows[0];
    }
    if (path === "/v1/account-export" && request.method === "GET") {
      const tables = {}; let requests = 0; let archiveBytes = 0;
      for (const [name, order] of Object.entries(EXPORT_TABLES)) {
        const rows = [];
        while (true) {
          if (++requests > 40) throw new ApiError(413, "This account needs a server export. No partial archive was produced.");
          const page = await table(name, { select: "*", order: `${order}.asc`, limit: 500, offset: rows.length });
          if (!Array.isArray(page) || page.some(row => !row || typeof row !== "object" || Array.isArray(row))) throw new ApiError(502, "Account export returned invalid data.");
          const clean = page.map(({ user_id, ...row }) => row);
          archiveBytes += new TextEncoder().encode(JSON.stringify(clean)).length;
          if (archiveBytes > MAX_RESPONSE_BYTES - 64000) throw new ApiError(413, "This account needs a server export. No partial archive was produced.");
          rows.push(...clean);
          if (page.length < 500) break;
        }
        tables[name] = rows;
      }
      return { schema_version: 1, exported_at: new Date(now()).toISOString(), scope: "All rows visible to the signed-in user in the listed application tables.", consistency: "Read-only sequential export; concurrent account edits may span tables. No auth-provider profile or credential is included.", tables, counts: Object.fromEntries(Object.entries(tables).map(([key, rows]) => [key, rows.length])) };
    }
    const computeRoutes = new Map([
      ["POST /v1/plan-jobs", 55000],
      ["POST /v1/plans/current", 165000], ["POST /v1/plans/exit-guidance", 55000],
      ["POST /v1/planning-profiles/auto-plan", 55000], ["POST /v1/planning-profiles/estimate", 10000],
      ["GET /v1/market/vix", 25000],
      ["GET /v1/recommendation-journal", 55000],
    ]);
    const companyResearch = /^\/v1\/research\/company\/[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?$/.test(path);
    const jobStatus = path.match(/^\/v1\/plan-jobs\/([0-9a-f-]{36})$/i);
    const timeout = jobStatus && request.method === "GET"
      ? (uuid(jobStatus[1]), 55000) : companyResearch && request.method === "GET"
        ? 25000 : computeRoutes.get(`${request.method} ${path}`);
    if (timeout) {
      if (!config.compute_configured) throw new ApiError(503, config.compute_message);
      const target = `${httpsOrigin(env.STOCK_COMP_PYTHON_API_URL, "Strategy service")}${path}`;
      const body = request.method === "GET" ? undefined : JSON.stringify(await input(request));
      // Send only the verified user's token. No Site identity header, cookie,
      // browser Origin, service-role key, or arbitrary destination is forwarded.
      let response, value;
      try {
        response = await fetcher(target, { method: request.method, headers: { Authorization: authorization, "Content-Type": "application/json" }, body, redirect: "manual", signal: AbortSignal.timeout(timeout) });
        if (response.status >= 300 && response.status < 400) throw new ApiError(502, "The strategy service returned an unexpected redirect.");
        value = await serviceJson(response);
      } catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError(error?.name === "TimeoutError" || error?.name === "AbortError" ? 504 : 502, "The strategy service could not be reached. It may be starting on the free host. No result was confirmed; refresh before retrying.");
      }
      if (!response.ok) {
        if (response.status >= 500) console.error("strategy_service_upstream", { path, status: response.status });
        if (response.status === 524) throw new ApiError(504, "The hosting proxy timed out before the scan responded. Refresh saved plans before retrying; no result was confirmed.");
        throw new ApiError([401, 403, 409, 422, 429, 503, 504].includes(response.status) ? response.status : 502, typeof value?.detail === "string" ? value.detail.slice(0, 600) : "The strategy service is unavailable.");
      }
      return value;
    }
    throw new ApiError(404, "This application endpoint is unavailable.");
  };
}
