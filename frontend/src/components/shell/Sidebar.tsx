import { useState } from "react";
import { NavLink } from "react-router-dom";
import ChatList from "./ChatList";
import ProjectSwitcher from "./ProjectSwitcher";

const PRIMARY: { to: string; label: string; icon: string; end?: boolean }[] = [
  { to: "/app", label: "Account Manager", icon: "💬", end: true },
  { to: "/app/campaigns", label: "Campaigns", icon: "🚀" },
  { to: "/app/approvals", label: "Approvals", icon: "✅" },
  { to: "/app/results", label: "Results", icon: "📊" },
];

const SECONDARY: { to: string; label: string; icon: string }[] = [
  { to: "/app/start", label: "Start Here", icon: "📖" },
  { to: "/app/jobs", label: "Jobs", icon: "⚙" },
  { to: "/app/knowledge", label: "Knowledge", icon: "📚" },
  { to: "/app/settings", label: "Settings", icon: "🔧" },
];

function navClass(collapsed: boolean) {
  return ({ isActive }: { isActive: boolean }) =>
    [
      "flex items-center gap-2 rounded-sm px-2 py-1.5 text-bodysm",
      isActive ? "bg-elevated font-semibold text-ink" : "text-inksecondary hover:bg-elevated hover:text-ink",
      collapsed && "justify-center",
    ].join(" ");
}

/**
 * DEV-004 W2: left nav — ProjectSwitcher + ChatList + primary/secondary nav.
 * Collapsible to icon-only via the toggle (CSS class switch); the AppShell
 * drawer handles tablet/mobile overlay behavior.
 */
export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <nav
      aria-label="Primary"
      className={["flex h-full flex-col gap-3 p-3", collapsed && "sidebar-collapsed"].join(" ")}
    >
      <div className={collapsed ? "flex justify-center" : ""}>
        {collapsed ? (
          <span aria-hidden="true" className="text-bodysm font-semibold">▦</span>
        ) : (
          <ProjectSwitcher />
        )}
      </div>

      <div className={collapsed ? "hidden" : "min-h-0 flex-1 overflow-y-auto border-t border-linesubtle pt-3"}>
        <ChatList />
      </div>

      <div className="flex flex-col gap-0.5 border-t border-linesubtle pt-3">
        {PRIMARY.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={navClass(collapsed)} title={item.label}>
            <span aria-hidden="true">{item.icon}</span>
            {!collapsed && <span>{item.label}</span>}
          </NavLink>
        ))}
      </div>

      <div className="flex flex-col gap-0.5 border-t border-linesubtle pt-3">
        {SECONDARY.map((item) => (
          <NavLink key={item.to} to={item.to} className={navClass(collapsed)} title={item.label}>
            <span aria-hidden="true">{item.icon}</span>
            {!collapsed && <span>{item.label}</span>}
          </NavLink>
        ))}
      </div>

      <div className="mt-auto border-t border-linesubtle pt-2">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          className="w-full rounded-sm px-2 py-1.5 text-meta text-inksecondary hover:bg-elevated"
        >
          {collapsed ? "▶" : "◀ Collapse"}
        </button>
      </div>
    </nav>
  );
}
