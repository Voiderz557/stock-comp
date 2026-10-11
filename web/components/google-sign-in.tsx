"use client";

import Script from "next/script";
import { useEffect, useRef, useState } from "react";
import { getSupabaseClient } from "@/lib/supabase";

type GoogleIdentity = {
  accounts: {
    id: {
      initialize: (options: {
        client_id: string;
        nonce: string;
        callback: (response: { credential?: string }) => void;
      }) => void;
      renderButton: (element: HTMLElement, options: Record<string, string>) => void;
    };
  };
};

const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID?.trim() ?? "";

async function createNonce(): Promise<{ raw: string; hashed: string }> {
  const raw = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
  const hashed = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  return { raw, hashed };
}

export function GoogleSignIn({ onError }: { onError: (message: string) => void }) {
  const buttonRef = useRef<HTMLDivElement>(null);
  const [scriptReady, setScriptReady] = useState(false);

  useEffect(() => {
    if (!scriptReady || !googleClientId || !buttonRef.current) return;
    let active = true;

    async function render() {
      try {
        const google = (window as Window & { google?: GoogleIdentity }).google;
        if (!google) throw new Error("Google sign-in is unavailable. Use the redirect link below.");
        const nonce = await createNonce();
        if (!active || !buttonRef.current) return;
        google.accounts.id.initialize({
          client_id: googleClientId,
          nonce: nonce.hashed,
          callback: async (response) => {
            try {
              if (!response.credential) throw new Error("Google did not return a sign-in credential.");
              const client = getSupabaseClient();
              if (!client) throw new Error("Google sign-in is not configured.");
              const { error } = await client.auth.signInWithIdToken({
                provider: "google",
                token: response.credential,
                nonce: nonce.raw,
              });
              if (error) throw error;
            } catch (error) {
              onError(`${error instanceof Error ? error.message : "Google sign-in failed."} Try the redirect link below.`);
            }
          },
        });
        buttonRef.current.replaceChildren();
        google.accounts.id.renderButton(buttonRef.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          text: "continue_with",
          width: "360",
        });
      } catch (error) {
        if (active) onError(error instanceof Error ? error.message : "Google sign-in could not load.");
      }
    }

    void render();
    return () => { active = false; };
  }, [scriptReady, onError]);

  if (!googleClientId) return null;
  return (
    <>
      <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" onReady={() => setScriptReady(true)} onError={() => onError("Google sign-in could not load. Use the redirect link below.")} />
      <div className="google-signin-button" ref={buttonRef} aria-label="Continue with Google" />
    </>
  );
}
