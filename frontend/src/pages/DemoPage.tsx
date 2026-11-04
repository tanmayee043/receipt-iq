import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, demoImageUrl } from "../api";
import { InfoIcon } from "../components/Icons";
import { RoutePath } from "../components/RoutePath";
import { EmptyState, ErrorState, PageHeading, Skeleton } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";

export const DEMO_SAMPLES_KEY = ["demo-samples"];

export function DemoNote() {
  return (
    <p className="mb-6 flex gap-2 rounded-control border border-border bg-surface px-4 py-3">
      <span className="mt-0.5 text-warn">
        <InfoIcon />
      </span>
      <span>
        <strong className="font-semibold">Demo.</strong> These answers were saved from real runs, so
        no provider is called and nothing you change is stored on the server.
      </span>
    </p>
  );
}

export function Attribution() {
  return (
    <p className="mt-6 max-w-[68ch] text-[13px] text-text-2">
      Sample receipts are from the{" "}
      <a href="https://github.com/clovaai/cord" className="underline hover:text-text">
        CORD dataset
      </a>{" "}
      (Park et al., 2019), under{" "}
      <a href="https://creativecommons.org/licenses/by/4.0/" className="underline hover:text-text">
        CC BY 4.0
      </a>
      , resized and recompressed for the web.
    </p>
  );
}

export function DemoPage() {
  const samples = useQuery({ queryKey: DEMO_SAMPLES_KEY, queryFn: api.demoSamples });

  return (
    <>
      <PageHeading title="Turn a receipt into a split bill">
        Receipt IQ reads a photo with whichever provider is cheapest and accurate enough, checks the
        arithmetic, and sends anything that doesn't add up to a person. Pick a receipt to see it.
      </PageHeading>
      <DemoNote />
      {samples.isPending && (
        <div role="status" aria-label="Loading the samples" className="grid gap-2">
          {[0, 1, 2, 3].map((row) => (
            <Skeleton key={row} className="h-24" />
          ))}
        </div>
      )}
      {samples.isError && !samples.data && (
        <ErrorState
          title="Couldn't load the samples"
          error={samples.error}
          onRetry={() => void samples.refetch()}
        />
      )}
      {samples.data?.length === 0 && (
        <EmptyState title="No samples are available">
          The sample receipts haven't been built on this server. Run scripts/build_demo.py to add
          them.
        </EmptyState>
      )}
      {samples.data && samples.data.length > 0 && (
        <ul className="overflow-hidden rounded-control border border-border">
          {samples.data.map((sample) => (
            <li key={sample.id} className="border-t border-border first:border-t-0">
              <Link
                to={`/demo/${sample.id}`}
                className="flex gap-3 px-4 py-3 no-underline transition-colors duration-150 hover:bg-surface"
              >
                <img
                  src={demoImageUrl(sample.id)}
                  alt=""
                  loading="lazy"
                  className="h-20 w-14 shrink-0 rounded-badge border border-border bg-surface object-cover"
                />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{sample.title}</span>
                  <span className="block text-text-2">{sample.note}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-0.5 text-[13px]">
                    <StatusBadge status={sample.status} />
                    <RoutePath attempts={sample.attempts} />
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <Attribution />
    </>
  );
}
