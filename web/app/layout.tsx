import type { Metadata } from "next";
import { HostedBootstrap } from "@/components/hosted-bootstrap";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://127.0.0.1:4173"),
  title: "Stock Competition",
  description: "A clear daily command center for research and paper trading.",
  icons: { icon: "/favicon.svg" },
  alternates: { canonical: "/" },
  robots: { index: true, follow: true },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body><HostedBootstrap>{children}</HostedBootstrap></body>
    </html>
  );
}
