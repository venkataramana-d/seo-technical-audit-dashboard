import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AuditProvider } from "@/lib/state/AuditContext";
import { AuthProvider } from "@/lib/state/AuthContext";
import { AppShell } from "@/components/AppShell";
import { themeInitScript } from "@/components/ThemeToggle";

// Inter is the canonical modern-SaaS UI face (Linear/Vercel-family). next/font
// self-hosts the files at build time - no runtime request to Google, so it does
// not need a CSP allowance. `font-display: swap` avoids invisible text.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

// Monospace for URLs, code, and raw values in the audit output.
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});

// Canonical production URL (overridable at build via NEXT_PUBLIC_SITE_URL, or
// APP_BASE_URL server-side). Keep this in sync with the live Vercel domain.
const SITE_URL =
  process.env.NEXT_PUBLIC_SITE_URL ||
  process.env.APP_BASE_URL ||
  "https://seo-technical-audit-dashboard-venkat-ramana.vercel.app";
const SITE_NAME = "SEO Technical Audit Dashboard";
const SITE_DESCRIPTION = "Enterprise-grade SEO technical audit tool";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: SITE_NAME, template: `%s · ${SITE_NAME}` },
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  keywords: ["SEO audit", "technical SEO", "site health", "crawlability", "on-page SEO", "Core Web Vitals"],
  // Internal tool: not meant for public search results (see app/robots.ts
  // for the matching robots.txt disallow-all).
  robots: { index: false, follow: false },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: SITE_NAME,
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
  },
};

export const viewport: Viewport = {
  themeColor: "#6366F1",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // proxy.ts stamps a per-request CSP nonce on the request headers;
  // this inline script needs it or the CSP's script-src blocks it.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html
      lang="en"
      className={`h-full antialiased ${inter.variable} ${jetbrainsMono.variable}`}
      // themeInitScript sets the theme class/attribute on <html> before React
      // hydrates, so the server and client markup differ by design - suppress
      // the resulting (expected) hydration warning on this element only.
      suppressHydrationWarning
    >
      <head>
        {/* Browsers strip the `nonce` attribute from the DOM after applying the
            CSP (a security measure), so the client reads nonce="" while the
            server rendered the real per-request nonce from proxy.ts - a
            well-known false-positive hydration mismatch. suppressHydrationWarning
            silences it; the script itself is applied correctly (CSP allows the
            nonce, the theme initializes). */}
        <script
          nonce={nonce}
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: themeInitScript }}
        />
      </head>
      <body className="min-h-full flex flex-col bg-[var(--seo-app-bg)] text-[var(--seo-text)]">
        <AuthProvider>
          <AuditProvider>
            <AppShell>{children}</AppShell>
          </AuditProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
