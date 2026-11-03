import { useLocation, useNavigate } from "react-router";

import { LoginForm } from "../components/LoginForm";
import { PageHeading } from "../components/States";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/review";

  return (
    <>
      <PageHeading title="Log in">
        Uploading and reviewing receipts spend provider quota, so they're only open to the owner.
      </PageHeading>
      <LoginForm onSuccess={() => void navigate(from, { replace: true })} />
    </>
  );
}
