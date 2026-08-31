"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/search", label: "Search" },
  { href: "/documents", label: "Documents" },
  { href: "/forms", label: "Forms" },
];

export default function Nav() {
  const path = usePathname();
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
    <nav className="nav">
      <div className="brand">
        Nyaya
        <small>Bharatiya Nagarik Suraksha Sanhita, 2023</small>
      </div>
      {LINKS.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          data-active={l.href === "/" ? path === "/" : path.startsWith(l.href)}
        >
          {l.label}
        </Link>
      ))}
      <button
        onClick={toggleTheme}
        style={{ marginLeft: "auto", padding: "3px 9px", fontSize: "12px" }}
        title={`Theme: ${theme}`}
      >
        {theme === "light" ? "☀️" : theme === "dark" ? "🌙" : "⚙️"}
      </button>
    </nav>
  );
}
