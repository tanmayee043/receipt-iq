import type { ReactNode } from "react";
import { Link } from "react-router";

import { useDemoOnly, useLoggedIn } from "../session";
import { LoginForm } from "./LoginForm";
import { Skeleton } from "./States";

/** On the hosted demo, in place of a login that has nothing behind it. */
export function DemoOnlyNotice() {
  return (
    <section className="rounded-control border border-border bg-surface px-5 py-6">
      <h2 className="text-base font-semibold">Not on this public copy</h2>
      <p className="mt-1 max-w-prose text-text-2">
        Uploading and reviewing receipts call paid providers, so they only run on the owner's
        own copy. Everything here is the <Link to="/demo">demo</Link> and the{" "}
        <Link to="/leaderboard">leaderboard</Link>.
      </p>
    </section>
  );
}

export function RequireLogin({
  title,
  why,
  children,
}: {
  title: string;
  why: string;
  children: ReactNode;
}) {
  const loggedIn = useLoggedIn();
  const demoOnly = useDemoOnly();
  if (demoOnly) return <DemoOnlyNotice />;
  if (loggedIn === undefined) {
    return (
      <div role="status" aria-label="Checking your login">
        <Skeleton className="h-40" />
      </div>
    );
  }
  if (!loggedIn) {
    return (
      <section className="rounded-control border border-border bg-surface px-5 py-6">
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="mt-1 mb-4 max-w-prose text-text-2">{why}</p>
        <LoginForm />
      </section>
    );
  }
  return <>{children}</>;
}
