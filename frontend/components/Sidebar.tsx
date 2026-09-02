"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  { href: "/", label: "Chat", icon: "💬" },
  { href: "/search", label: "Search", icon: "🔍" },
  { href: "/documents", label: "Documents", icon: "📄" },
  { href: "/forms", label: "Forms", icon: "📋" },
];

export default function Sidebar() {
  const path = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");

  useEffect(() => {
    const saved = localStorage.getItem("theme") as "light" | "dark" | "system" | null;
    const initial = saved || "system";
    setTheme(initial);
    applyTheme(initial);
  }, []);

  function applyTheme(t: "light" | "dark" | "system") {
    const html = document.documentElement;
    if (t === "system") {
      html.removeAttribute("data-theme");
    } else {
      html.setAttribute("data-theme", t);
    }
  }

  function toggleTheme() {
    const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
    setTheme(next);
    localStorage.setItem("theme", next);
    applyTheme(next);
  }

  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="sidebar-header">
        <button
          className="collapse-btn"
          onClick={() => setCollapsed(!collapsed)}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? "→" : "←"}
        </button>
      </div>

      <nav className="sidebar-nav">
        {LINKS.map((link) => {
          const isActive = link.href === "/" ? path === "/" : path.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`sidebar-link ${isActive ? "active" : ""}`}
              title={link.label}
            >
              <span className="icon">{link.icon}</span>
              {!collapsed && <span className="label">{link.label}</span>}
            </Link>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <button
          className="sidebar-theme-btn"
          onClick={toggleTheme}
          title={`Theme: ${theme}`}
        >
          {theme === "light" ? "☀️" : theme === "dark" ? "🌙" : "⚙️"}
        </button>
      </div>
    </aside>
  );
}
