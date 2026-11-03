import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api, ApiError } from "../api";
import { SESSION_KEY } from "../session";
import { Button } from "./States";

export function LoginForm({ onSuccess }: { onSuccess?: () => void }) {
  const [password, setPassword] = useState("");
  const queryClient = useQueryClient();
  const fieldId = useId();
  const errorId = useId();

  const login = useMutation({
    mutationFn: api.login,
    onSuccess: () => {
      setPassword("");
      queryClient.setQueryData(SESSION_KEY, { logged_in: true });
      onSuccess?.();
    },
  });
  const failure = login.error instanceof ApiError ? login.error.message : null;

  return (
    <form
      className="max-w-sm"
      onSubmit={(event) => {
        event.preventDefault();
        if (password) login.mutate(password);
      }}
    >
      <label htmlFor={fieldId} className="block text-[13px] font-medium">
        Owner password
      </label>
      <input
        id={fieldId}
        type="password"
        autoComplete="current-password"
        required
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        aria-invalid={failure !== null}
        aria-describedby={failure ? errorId : undefined}
        className="mt-1 min-h-10 w-full rounded-control border border-border bg-raised px-3 text-base transition-colors duration-150 hover:border-text-2 sm:text-sm"
      />
      {failure && (
        <p id={errorId} role="alert" className="mt-2 text-down">
          {failure}
        </p>
      )}
      <div className="mt-3">
        <Button type="submit" variant="primary" disabled={login.isPending}>
          {login.isPending ? "Logging in…" : "Log in"}
        </Button>
      </div>
    </form>
  );
}
