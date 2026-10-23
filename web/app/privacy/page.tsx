import type { Metadata } from "next";
import { LegalPage } from "@/components/legal-page";

export const metadata: Metadata = { title: "Privacy | Stock Competition" };

export default function PrivacyPage() {
  const privacyContact = process.env.NEXT_PUBLIC_PRIVACY_CONTACT?.trim();
  return (
    <LegalPage title="Privacy notice" summary="What this research and paper-trading application collects, why it is used, and what is deliberately excluded.">
      <section><h2>Data we collect</h2><p>When you sign in, Supabase and your chosen identity provider supply an account identifier and may supply your name, email address, and profile image. We store the competition-account details you enter, such as starting capital, cash, holdings, paper actions, saved plans, and recommendation history.</p></section>
      <section><h2>Technical data</h2><p>The API and hosting providers may process IP addresses, request timestamps, browser information, error details, and security logs needed to operate and protect the service. The current prototype does not use advertising pixels or behavioral analytics.</p></section>
      <section><h2>How data is used</h2><p>Data is used to authenticate you, isolate your account, restore your portfolio, generate research and paper-trading views, prevent abuse, and diagnose failures. Algorithm signals are research outputs; they do not execute real trades.</p></section>
      <section><h2>Processors and sharing</h2><p>Supabase provides authentication and user-scoped database storage. Google processes information when you choose Google sign-in. Hosting and market-data providers process only what is needed to deliver their services. We do not sell personal information or share it for targeted advertising.</p></section>
      <section><h2>What we do not request</h2><p>The current application does not request brokerage passwords, bank credentials, payment-card data, or authority to place real trades.</p></section>
      <section><h2>Optional research checks</h2><p>When you request outside company information, our server may send the ticker or company filing identifier to the SEC. When you load recommendation outcomes, it may request ticker symbols and historical date ranges from the existing market-data provider. These requests do not send your identity, cash, quantities, account history, or authentication tokens to those data providers. Opening a source link connects your browser directly to that source. Research checks do not run automatically and do not change your portfolio or strategy signals.</p></section>
      <section><h2>Retention and control</h2><p>Account records are retained while the account is active and for limited backup, security, and legal periods. A self-service deletion control is not available in this prototype and must be completed before general public launch.</p></section>
      <section><h2>Contact</h2><p>{privacyContact ? <>Privacy requests: <a href={`mailto:${privacyContact}`}>{privacyContact}</a>.</> : "A monitored privacy contact has not been configured. Public launch is blocked until one is published."}</p></section>
    </LegalPage>
  );
}
