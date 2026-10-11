import { createClient, type SupabaseClient } from "@supabase/supabase-js";

export let supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim() ?? "";
export let publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() ?? "";

export let supabaseConfigured = Boolean(supabaseUrl && publishableKey);

export type OAuthAvailability = {
  google: boolean;
  azure: boolean;
};

let browserClient: SupabaseClient | null = null;

export function configureSupabase(url: string, key: string): void {
  const nextUrl = url.trim();
  const nextKey = key.trim();
  if (nextUrl === supabaseUrl && nextKey === publishableKey) return;
  // Reconfiguration must not sign out or remove the persisted PKCE/session data.
  browserClient?.auth.stopAutoRefresh();
  browserClient = null;
  supabaseUrl = nextUrl;
  publishableKey = nextKey;
  supabaseConfigured = Boolean(supabaseUrl && publishableKey);
}

export function getSupabaseClient(): SupabaseClient | null {
  if (!supabaseConfigured || typeof window === "undefined") return null;
  if (!browserClient) {
    browserClient = createClient(supabaseUrl, publishableKey, {
      auth: {
        flowType: "pkce",
        detectSessionInUrl: false,
        persistSession: true,
        autoRefreshToken: true,
      },
    });
  }
  return browserClient;
}

export async function getOAuthAvailability(): Promise<OAuthAvailability> {
  if (!supabaseConfigured) return { google: false, azure: false };

  try {
    const response = await fetch(`${supabaseUrl}/auth/v1/settings`, {
      headers: { apikey: publishableKey },
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) return { google: false, azure: false };

    const settings = await response.json() as {
      external?: { google?: boolean; azure?: boolean };
    };
    return {
      google: settings.external?.google === true,
      azure: settings.external?.azure === true,
    };
  } catch {
    return { google: false, azure: false };
  }
}

export async function startOAuth(provider: "google" | "azure") {
  const client = getSupabaseClient();
  if (!client) throw new Error("OAuth is not configured yet.");
  const redirectTo = `${window.location.origin}/auth/callback`;
  const { error } = await client.auth.signInWithOAuth({
    provider,
    options: {
      redirectTo,
      scopes: provider === "azure" ? "email" : undefined,
    },
  });
  if (error) throw error;
}

export async function sendMagicLink(email: string) {
  const client = getSupabaseClient();
  if (!client) throw new Error("Email sign in is not configured yet.");
  const { error } = await client.auth.signInWithOtp({
    email,
    options: {
      emailRedirectTo: `${window.location.origin}/auth/callback`,
      shouldCreateUser: true,
    },
  });
  if (error) throw error;
}
