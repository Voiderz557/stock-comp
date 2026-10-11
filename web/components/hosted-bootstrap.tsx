"use client";

import { useEffect, useState, type ReactNode } from "react";
import { configureSupabase } from "@/lib/supabase";

const hostedRuntimeEnabled = process.env.NEXT_PUBLIC_HOSTED_RUNTIME_CONFIG === "true";
const maxConfigBytes = 16_384;
const computeUnavailableMessage = "Daily scanning and automatic stop guidance are waiting for the strategy service setup. Saved accounts and history remain available.";

type BootstrapState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; computeConfigured: boolean };

function isPublicKey(key: string): boolean {
  if (/^sb_publishable_[A-Za-z0-9_-]+$/.test(key)) return true;
  // Existing projects may still use the public anon JWT rather than a new key.
  if (!/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(key)) return false;
  try {
    const payload = JSON.parse(atob(key.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload?.role === "anon";
  } catch {
    return false;
  }
}

async function readConfig(response: Response) {
  if (!response.ok || !response.body
    || response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json"
    || Number(response.headers.get("content-length")) > maxConfigBytes) {
    void response.body?.cancel().catch(() => {});
    throw new Error("Site configuration is unavailable.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let bytes = 0;
  let text = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > maxConfigBytes) throw new Error("Site configuration is unavailable.");
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
  } finally {
    // Also cancel an oversized or malformed stream before releasing the reader.
    void reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  const config: unknown = JSON.parse(text);
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    throw new Error("Site configuration is unavailable.");
  }
  const data = config as Record<string, unknown>;
  if (data.storage_configured !== true
    || typeof data.compute_configured !== "boolean"
    || typeof data.compute_message !== "string" || data.compute_message.length > 1_024
    || typeof data.supabase_url !== "string" || data.supabase_url.length > 2_048
    || typeof data.supabase_publishable_key !== "string" || data.supabase_publishable_key.length > 4_096) {
    throw new Error("Site configuration is unavailable.");
  }
  const url = new URL(data.supabase_url.trim());
  const key = data.supabase_publishable_key.trim();
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash
    || !isPublicKey(key)) {
    throw new Error("Site configuration is unavailable.");
  }
  return { url: data.supabase_url.trim(), key, computeConfigured: data.compute_configured };
}

export function HostedBootstrap({ children }: Readonly<{ children: ReactNode }>) {
  // The build flag yields identical server HTML and first hydration output.
  const [state, setState] = useState<BootstrapState>(() => hostedRuntimeEnabled
    ? { status: "loading" }
    : { status: "ready", computeConfigured: true });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!hostedRuntimeEnabled) return;
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => {
      controller.abort();
      if (active) setState({ status: "error" });
    }, 10_000);

    async function initialize() {
      try {
        const response = await fetch("/api/v1/config", {
          cache: "no-store",
          credentials: "same-origin",
          redirect: "error",
          signal: controller.signal,
        });
        const config = await readConfig(response);
        if (!active || controller.signal.aborted) return;
        configureSupabase(config.url, config.key);
        setState({ status: "ready", computeConfigured: config.computeConfigured });
      } catch {
        // Never display response bodies, backend messages, or thrown credentials.
        if (active) setState({ status: "error" });
      } finally {
        clearTimeout(timeout);
      }
    }
    void initialize();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timeout);
    };
  }, [attempt]);

  if (state.status !== "ready") {
    return (
      <main className="callback-shell">
        <section className="callback-card">
          {state.status === "loading" ? (
            <p role="status">Connecting to your saved accounts…</p>
          ) : (
            <>
              <p role="alert">The site configuration or account storage is unavailable. Please retry.</p>
              <button type="button" onClick={() => {
                setState({ status: "loading" });
                setAttempt((value) => value + 1);
              }}>Retry</button>
            </>
          )}
        </section>
      </main>
    );
  }

  return (
    <>
      {!state.computeConfigured && (
        <div role="status" style={{ padding: "12px 20px", background: "#fff3cd", color: "#594300" }}>
          {computeUnavailableMessage}
        </div>
      )}
      {children}
    </>
  );
}
