"use client";

import { useEffect, useState } from "react";
import { getSupabaseClient } from "@/lib/supabase";
import { LegalLinks } from "@/components/legal-links";

export default function OAuthCallbackPage() {
  const [message, setMessage] = useState("Finishing sign in…");

  useEffect(() => {
    async function finish() {
      const client = getSupabaseClient();
      const code = new URL(window.location.href).searchParams.get("code");
      if (!client || !code) {
        setMessage("Sign in could not be completed. Return to the home page and try again.");
        return;
      }
      const { error } = await client.auth.exchangeCodeForSession(code);
      if (error) {
        setMessage("Sign in could not be completed. Return to the home page and try again.");
        return;
      }
      window.location.replace("/");
    }
    void finish();
  }, []);

  return (
    <main className="callback-shell">
      <section className="callback-card">
        <span className="eyebrow">Secure sign in</span>
        <h1>{message}</h1>
        <a href="/">Return home</a>
        <LegalLinks />
      </section>
    </main>
  );
}
