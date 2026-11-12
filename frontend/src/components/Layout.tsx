import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { api } from "../api";
import { SESSION_KEY, useDemoOnly, useLoggedIn } from "../session";
import { useTheme } from "../theme";
import { MoonIcon, SunIcon } from "./Icons";

// Visitors only see links they can use; the owner's pages would just ask them to log in.
const PAGES = [
  { to: "/demo", label: "Demo", title: "Demo", owner: false },
  { to: "/upload", label: "Upload", title: "Upload a receipt", owner: true },
  { to: "/review", label: "Review", title: "Review queue", owner: true },
  { to: "/leaderboard", label: "Leaderboard", title: "Model leaderboard", owner: false },
  { to: "/routing-log", label: "Routing log", title: "Routing log", owner: true },
];

function ThemeToggle() {
  const [theme, toggle] = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="inline-flex size-10 items-center justify-center rounded-control text-text-2 transition-colors duration-150 hover:bg-surface hover:text-text"
    >
      {theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

function AccountControl() {
  const loggedIn = useLoggedIn();
  const demoOnly = useDemoOnly();
  const location = useLocation();
  const queryClient = useQueryClient();
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      queryClient.setQueryData(SESSION_KEY, { logged_in: false });
      // What only the owner may see leaves the browser's memory too.
      queryClient.removeQueries({
        predicate: (query) => !["session", "leaderboard"].includes(String(query.queryKey[0])),
      });
    },
  });

  const look =
    "inline-flex min-h-10 items-center rounded-control px-3 font-medium text-text-2 transition-colors duration-150 hover:bg-surface hover:text-text";
  if (loggedIn) {
    return (
      <button type="button" className={look} onClick={() => logout.mutate()}>
        Log out
      </button>
    );
  }
  // Hidden until the session is known, so it doesn't flash "Log in" at someone logged in.
  if (loggedIn === undefined || demoOnly || location.pathname === "/login") return null;
  return (
    <Link to="/login" state={{ from: location.pathname }} className={`${look} no-underline`}>
      Log in
    </Link>
  );
}

function Navigation() {
  const loggedIn = useLoggedIn();
  return (
    <nav aria-label="Main">
      <ul className="flex gap-1">
        {PAGES.filter((page) => loggedIn || !page.owner).map((page) => (
          <li key={page.to}>
            <NavLink
              to={page.to}
              className={({ isActive }) =>
                `inline-flex min-h-10 items-center rounded-control px-2 font-medium whitespace-nowrap no-underline transition-colors duration-150 sm:px-3 ${
                  isActive
                    ? "bg-accent-subtle text-accent-strong"
                    : "text-text-2 hover:bg-surface hover:text-text"
                }`
              }
            >
              {page.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function Layout() {
  const { pathname } = useLocation();
  const main = useRef<HTMLElement>(null);
  const firstRender = useRef(true);

  useEffect(() => {
    const page = PAGES.find((candidate) => candidate.to === pathname);
    document.title = page ? `${page.title} · Receipt IQ` : "Receipt IQ";
    // Tells a screen reader the page changed, without stealing focus on the first load.
    if (firstRender.current) {
      firstRender.current = false;
    } else {
      main.current?.focus();
    }
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-10 focus:rounded-control focus:bg-raised focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-[1120px] flex-wrap items-center gap-x-4 px-4 py-2 sm:px-6">
          <Link to="/" className="py-2 text-base font-semibold no-underline">
            Receipt IQ
          </Link>
          <div className="order-last -mx-1 w-full overflow-x-auto pb-1 sm:order-none sm:mx-0 sm:w-auto sm:pb-0">
            <Navigation />
          </div>
          <div className="ml-auto flex items-center gap-1">
            <AccountControl />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main
        id="main"
        ref={main}
        tabIndex={-1}
        className="mx-auto max-w-[1120px] px-4 py-6 sm:px-6 sm:py-8"
      >
        <Outlet />
      </main>
    </>
  );
}
