"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentType, SVGProps } from "react";
import {
  GaugeIcon,
  GlobeIcon,
  ListChecksIcon,
  ScanIcon,
  SearchIcon,
  SettingsIcon,
  ShieldIcon,
} from "@/components/icons";
import { useAuth } from "@/lib/state/AuthContext";

type IconType = ComponentType<SVGProps<SVGSVGElement> & { size?: number }>;

interface NavItem {
  href: string;
  icon: IconType;
  label: string;
  badge?: string;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/", icon: GaugeIcon, label: "Dashboard" },
  { href: "/technical-audit", icon: ScanIcon, label: "New Audit" },
  { href: "/site-crawls", icon: GlobeIcon, label: "Site Crawls" },
  { href: "/results", icon: ListChecksIcon, label: "Results" },
  { href: "/explorer", icon: ScanIcon, label: "Explorer" },
  { href: "/settings", icon: SettingsIcon, label: "Settings" },
];

// The detail drill-down (/detail) belongs to the Results section; keep it lit
// while drilling into a single page's Links / Headings / Performance tabs.
function resolveActiveHref(pathname: string): string {
  if (pathname === "/detail") return "/results";
  if (pathname.startsWith("/site-crawls")) return "/site-crawls";
  return pathname;
}

/**
 * Persistent left rail - the workspace chrome. Rendered on a dark surface
 * (--seo-sidebar-*) regardless of app theme so it reads as a distinct frame
 * around the light/dark content column. `onNavigate` lets the mobile drawer
 * close itself after a link is tapped.
 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const active = resolveActiveHref(pathname);
  const { user } = useAuth();
  const navItems: NavItem[] =
    user?.role === "admin"
      ? [...NAV_ITEMS, { href: "/admin", icon: ShieldIcon, label: "Admin" }]
      : NAV_ITEMS;

  return (
    <div className="flex h-full flex-col gap-1 px-3.5 py-5">
      <Link
        href="/"
        onClick={onNavigate}
        className="flex items-center gap-2.5 px-2.5 pb-5 pt-1.5"
      >
        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[10px] bg-[image:var(--seo-gradient)] text-white shadow-[0_4px_14px_rgba(99,102,241,0.45)]">
          <SearchIcon size={19} />
        </span>
        <span className="leading-tight">
          <span className="block text-[14.5px] font-bold tracking-tight text-white">SEO Audit</span>
          <span className="block text-[11px] text-[var(--seo-sidebar-text)]">Technical Dashboard</span>
        </span>
      </Link>

      <div className="px-3 pb-1.5 pt-2 text-[10.5px] font-bold uppercase tracking-[0.09em] text-[var(--seo-sidebar-label)]">
        Workspace
      </div>

      <nav className="flex flex-col gap-0.5">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = active === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              aria-current={isActive ? "page" : undefined}
              className={`relative flex items-center gap-3 rounded-[9px] px-3 py-2.5 text-[13.5px] font-medium transition-colors ${
                isActive
                  ? "bg-[var(--seo-sidebar-active-bg)] text-[var(--seo-sidebar-text-active)] before:absolute before:-left-3.5 before:top-2 before:bottom-2 before:w-[3px] before:rounded-r-[3px] before:bg-[image:var(--seo-gradient)] before:content-['']"
                  : "text-[var(--seo-sidebar-text)] hover:bg-[var(--seo-sidebar-hover)] hover:text-[#E5E7F0]"
              }`}
            >
              <Icon size={17} className="shrink-0" />
              <span>{item.label}</span>
              {item.badge ? (
                <span className="ml-auto rounded-full bg-[var(--seo-error)] px-[7px] py-px text-[10.5px] font-bold text-white">
                  {item.badge}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto border-t border-[var(--seo-sidebar-border)] pt-4">
        <div className="px-3 pb-2.5">
          <div className="mb-1.5 flex items-center justify-between text-[11px] text-[var(--seo-sidebar-text)]">
            <span>Session storage</span>
            <span className="font-semibold text-[#E5E7F0]">Local</span>
          </div>
          <div className="h-[5px] overflow-hidden rounded-[4px] bg-[var(--seo-sidebar-track)]">
            <div className="h-full w-[38%] rounded-[4px] bg-[image:var(--seo-gradient)]" />
          </div>
        </div>
        <div className="flex items-center gap-3 rounded-[9px] px-3 py-2 text-[13px] text-[var(--seo-sidebar-text)]">
          <span className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-[image:var(--seo-gradient)] text-[11px] font-bold text-white">
            VR
          </span>
          <span className="truncate">Workspace</span>
        </div>
      </div>
    </div>
  );
}
