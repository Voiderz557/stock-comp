import type { Metadata } from "next";
import { LegalPage } from "@/components/legal-page";

export const metadata: Metadata = { title: "Cookies & storage | Stock Competition" };

export default function CookiesPage() {
  return (
    <LegalPage title="Cookies and browser storage" summary="The current prototype does not use advertising or analytics cookies.">
      <section><h2>Authentication storage</h2><p>The application stores the Supabase sign-in session in your browser so you remain signed in after a refresh. This is necessary to provide your private account. Google and Supabase may use their own cookies while completing OAuth sign-in.</p></section>
      <section><h2>Current tracking</h2><p>We have not added advertising cookies, cross-site tracking, or nonessential analytics. Because no optional tracking is active, the application does not currently display a consent banner that pretends to offer choices that do not exist.</p></section>
      <section><h2>Future changes</h2><p>Before optional analytics, advertising, or similar storage is enabled, the site must add a real consent manager with reject and withdraw controls and update this notice. Necessary security and authentication storage cannot be disabled while using a signed-in account.</p></section>
      <section><h2>Your controls</h2><p>You can sign out to end the active application session. Browser settings can clear site storage, which will sign you out and may remove local preferences.</p></section>
    </LegalPage>
  );
}
