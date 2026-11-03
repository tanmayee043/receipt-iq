import type { ReactNode } from "react";

import { useLoggedIn } from "../session";
import { LoginForm } from "./LoginForm";
import { Skeleton } from "./States";

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
