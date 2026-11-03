import { Link } from "react-router";

import { PageHeading } from "../components/States";

export function NotFoundPage() {
  return (
    <>
      <PageHeading title="That page doesn't exist" />
      <Link to="/leaderboard" className="font-medium text-accent underline">
        Go to the leaderboard
      </Link>
    </>
  );
}
