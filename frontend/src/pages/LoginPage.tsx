import { useLocation, useNavigate } from "react-router";

import { LoginForm } from "../components/LoginForm";
import { DemoOnlyNotice } from "../components/RequireLogin";
import { PageHeading } from "../components/States";
import { useDemoOnly } from "../session";

export function LoginPage() {
  const demoOnly = useDemoOnly();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/review";

  return (
    <>
      <PageHeading title="Log in">
        Uploading and reviewing receipts spend provider quota, so they're only open to the owner.
      </PageHeading>
      {demoOnly ? (
        <DemoOnlyNotice />
      ) : (
        <LoginForm onSuccess={() => void navigate(from, { replace: true })} />
      )}
    </>
  );
}
