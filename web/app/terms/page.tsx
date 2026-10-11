import type { Metadata } from "next";
import { LegalPage } from "@/components/legal-page";

export const metadata: Metadata = { title: "Terms | Stock Competition" };

export default function TermsPage() {
  return (
    <LegalPage title="Prototype terms" summary="Important limits for the research and paper-trading preview.">
      <section><h2>Research only</h2><p>The service provides experimental algorithm signals, backtests, and paper-trading tools. It is not financial, investment, tax, or legal advice and does not guarantee performance.</p></section>
      <section><h2>No real trading</h2><p>The public prototype cannot place real orders and must not be treated as connected to a brokerage account. You remain responsible for every financial decision made outside this application.</p></section>
      <section><h2>Data limitations</h2><p>Prices, classifications, strategy outputs, and market data may be delayed, incomplete, or wrong. Always verify freshness and source coverage before relying on a research result.</p></section>
      <section><h2>Acceptable use</h2><p>Do not attempt to access another user’s data, evade security controls, overload the service, scrape restricted data, or use the application unlawfully.</p></section>
      <section><h2>Prototype availability</h2><p>Features may change and the service may be interrupted during testing. Complete operator identity, support contact, deletion procedures, and jurisdiction-specific terms must be published before general public launch.</p></section>
    </LegalPage>
  );
}
