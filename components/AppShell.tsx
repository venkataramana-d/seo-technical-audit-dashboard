"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { GlobalSearch } from "@/components/GlobalSearch";
import { ThemeToggle } from "@/components/ThemeToggle";
import { PlusIcon } from "@/components/icons";
import { useAudit } from "@/lib/state/AuditContext";
import { useAuth } from "@/lib/state/AuthContext";
import { displayNameFromEmail } from "@/lib/format";

// Breadcrumb label for the current route (the detail drill-down reads as part
// of Results). Kept in sync with the nav items in Sidebar.tsx.
const PAGE_TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/technical-audit": "New Audit",
  "/results": "Results",
  "/detail": "Results",
  "/explorer": "Explorer",
  "/settings": "Settings",
  "/admin": "Admin",
};

function pageTitle(pathname: string): string {
  if (pathname.startsWith("/site-crawls")) return "Site Crawls";
  return PAGE_TITLES[pathname] ?? "Dashboard";
}

export function AppShell({ children }: { children: ReactNode }) {
  const { storageWarning } = useAudit();
  const { user, status, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const title = pageTitle(pathname);
  // Public, full-bleed routes that render without the app chrome and never
  // trigger the signed-out redirect (login + the emailed password-reset page).
  const isPublicRoute = pathname === "/login" || pathname === "/reset";

  // Send signed-out users to the login screen - but only when the auth backend
  // is actually reachable. "unavailable" means the Python API isn't running
  // (local `next dev`), where we keep the app open for frontend-only work.
  useEffect(() => {
    if (status === "anon" && !isPublicRoute) {
      router.replace("/login");
    }
  }, [status, isPublicRoute, router]);

  // The login / reset screens render full-bleed, without the app chrome.
  if (isPublicRoute) {
    return <>{children}</>;
  }

  // Avoid flashing the app before we know the session, or mid-redirect.
  if (status === "loading" || (status === "anon" && !isPublicRoute)) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--seo-app-bg)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--seo-border-strong)] border-t-[var(--seo-accent)]" aria-label="Loading" />
      </div>
    );
  }

  return (
    <div className="min-h-screen md:grid md:grid-cols-[248px_1fr]">
      {/* Desktop rail */}
      <aside className="sticky top-0 hidden h-screen bg-[var(--seo-sidebar-bg)] md:block">
        <Sidebar />
      </aside>

      {/* Mobile drawer */}
      {drawerOpen ? (
        <div className="fixed inset-0 z-50 md:hidden">
          <button
            type="button"
            aria-label="Close menu"
            className="absolute inset-0 bg-black/50"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="absolute left-0 top-0 h-full w-[264px] bg-[var(--seo-sidebar-bg)] shadow-[var(--seo-shadow-lg)]">
            <Sidebar onNavigate={() => setDrawerOpen(false)} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-col">
        {/* Command-bar topbar */}
        <header className="sticky top-0 z-40 flex items-center gap-3 border-b border-[var(--seo-border)] bg-[color-mix(in_srgb,var(--seo-app-bg)_82%,transparent)] px-4 py-2.5 backdrop-blur-md md:px-7">
          <button
            type="button"
            aria-label="Open menu"
            onClick={() => setDrawerOpen(true)}
            className="rounded-lg p-2 text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)] md:hidden"
          >
            <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>

          <div className="hidden items-center gap-2 text-[13px] text-[var(--seo-muted)] md:flex">
            <span>Workspace</span>
            <span>/</span>
            <span className="font-semibold text-[var(--seo-heading)]">{title}</span>
          </div>

          <div className="ml-auto flex items-center gap-2.5">
            <GlobalSearch />
            <button
              type="button"
              onClick={() => router.push("/technical-audit")}
              className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg btn-gradient px-3 py-2 text-[13px] font-semibold text-white"
            >
              <PlusIcon size={15} />
              <span className="hidden sm:inline">New Audit</span>
            </button>
            <ThemeToggle />
            {user ? (
              <div className="flex shrink-0 items-center gap-2 border-l border-[var(--seo-border)] pl-2.5">
                <div
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-bold text-white"
                  style={{ background: "var(--seo-gradient)" }}
                  title={user.email}
                  aria-hidden="true"
                >
                  {user.email.slice(0, 1).toUpperCase()}
                </div>
                <span
                  className="hidden max-w-[150px] truncate text-[13px] text-[var(--seo-text-light)] lg:inline"
                  title={user.email}
                >
                  {displayNameFromEmail(user.email)}
                </span>
                <button
                  type="button"
                  onClick={async () => {
                    await logout();
                    router.replace("/login");
                  }}
                  className="shrink-0 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[13px] font-medium text-[var(--seo-muted)] transition hover:bg-[var(--seo-card-hover)] hover:text-[var(--seo-text)]"
                >
                  Log out
                </button>
              </div>
            ) : null}
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-6 md:px-8">
          {storageWarning ? (
            <div className="mb-4 rounded-lg border border-[var(--seo-warning-border)] bg-[var(--seo-warning-bg)] px-3 py-2 text-sm text-[var(--seo-warning)]">
              {storageWarning}
            </div>
          ) : null}
          {children}
        </main>
      </div>
    </div>
  );
}
