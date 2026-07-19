import { NavLink, Outlet } from "react-router-dom";

import { HealthBadge } from "./HealthBadge";

/**
 * Navigation is grouped by the two independent systems the backend serves
 * (see docs/api-reference.md → System overview). They share no data — the
 * layout keeps them visually separate for the same reason.
 */
const NAV_SECTIONS = [
  {
    heading: "Calling Agent",
    links: [
      { to: "/campaigns", label: "Campaigns" },
      { to: "/call-records", label: "Call Records" },
    ],
  },
  {
    heading: "Helpdesk",
    links: [{ to: "/helpdesk", label: "Dashboard" }],
  },
];

function navLinkClasses({ isActive }: { isActive: boolean }): string {
  const base =
    "block rounded-md px-3 py-2 text-sm font-medium transition-colors";
  return isActive
    ? `${base} bg-slate-900 text-white`
    : `${base} text-slate-600 hover:bg-slate-100 hover:text-slate-900`;
}

/**
 * App shell: fixed sidebar with grouped nav, a header carrying the live API
 * health badge, and a scrollable content area where each page renders.
 */
export function AppLayout() {
  return (
    <div className="flex h-full min-h-screen bg-slate-50 text-slate-900">
      <aside className="flex w-64 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-5 py-4">
          <p className="text-sm font-semibold text-slate-900">
            Candidate Experience
          </p>
          <p className="text-xs text-slate-500">Automation Console</p>
        </div>

        <nav className="flex-1 space-y-6 px-3 py-5">
          {NAV_SECTIONS.map((section) => (
            <div key={section.heading}>
              <p className="px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                {section.heading}
              </p>
              <div className="space-y-1">
                {section.links.map((link) => (
                  <NavLink
                    key={link.to}
                    to={link.to}
                    className={navLinkClasses}
                  >
                    {link.label}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t border-slate-200 px-5 py-3 text-xs text-slate-400">
          Reference scaffold
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
          <h1 className="text-base font-semibold text-slate-900">
            Candidate Experience API Console
          </h1>
          <HealthBadge />
        </header>

        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
