import { useQuery } from "@tanstack/react-query";

import { api } from "./api";

export const SESSION_KEY = ["session"];

/** Whether the owner is logged in. Undefined until the first answer arrives. */
export function useLoggedIn(): boolean | undefined {
  const session = useQuery({ queryKey: SESSION_KEY, queryFn: api.session });
  return session.data?.logged_in;
}
