import type { ButtonHTMLAttributes, ReactNode } from "react";

import { ApiError } from "../api";
import { AlertIcon } from "./Icons";

export function Button({
  children,
  variant = "secondary",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" }) {
  const look =
    variant === "primary"
      ? "bg-accent text-on-accent hover:bg-accent-strong"
      : "border border-border bg-raised text-text hover:bg-surface";
  return (
    <button
      type="button"
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-control px-4 font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-55 ${look}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function Skeleton({ className }: { className: string }) {
  return <div aria-hidden="true" className={`skeleton ${className}`} />;
}

export function ErrorState({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: unknown;
  onRetry: () => void;
}) {
  const message = error instanceof ApiError ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="rounded-control border border-border bg-surface px-5 py-8">
      <p className="flex items-center gap-2 text-base font-semibold">
        <span className="text-down">
          <AlertIcon />
        </span>
        {title}
      </p>
      <p className="mt-1 max-w-prose text-text-2">{message}</p>
      <div className="mt-4">
        <Button onClick={onRetry}>Try again</Button>
      </div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-control border border-border bg-surface px-5 py-10">
      <p className="text-base font-semibold">{title}</p>
      <p className="mt-1 max-w-prose text-text-2">{children}</p>
    </div>
  );
}

export function PageHeading({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="text-xl font-semibold text-balance">{title}</h1>
      {children && <p className="mt-1 max-w-[68ch] text-text-2">{children}</p>}
    </header>
  );
}
