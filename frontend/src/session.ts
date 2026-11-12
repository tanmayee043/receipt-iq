import { useQuery } from "@tanstack/react-query";

import { api } from "./api";

export const SESSION_KEY = ["session"];

/** Whether the owner is logged in. Undefined until the first answer arrives. */
export function useLoggedIn(): boolean | undefined {
  const session = useQuery({ queryKey: SESSION_KEY, queryFn: api.session });
  return session.data?.logged_in;
}

/** True on the hosted demo, which has no owner pages to log in to. */
export function useDemoOnly(): boolean {
  const session = useQuery({ queryKey: SESSION_KEY, queryFn: api.session });
  return session.data?.demo_only ?? false;
}
