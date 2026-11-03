import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useState } from "react";

import { api, type Leaderboard, type Provider } from "../api";
import { InfoIcon, SortIcon } from "../components/Icons";
import { ErrorState, PageHeading, Skeleton } from "../components/States";
import {
  dollars,
  missesPolicy,
  percent,
  providerName,
  seconds,
  sortProviders,
  type SortKey,
} from "../format";

// The chart library is most of the download, and only this page uses it.
const AccuracyCostChart = lazy(() =>
  import("../components/AccuracyCostChart").then((module) => ({
    default: module.AccuracyCostChart,
  })),
);

const COLUMNS: { key: SortKey; label: string; format: (provider: Provider) => string }[] = [
  { key: "fully_correct", label: "Fully correct", format: (p) => percent(p.fully_correct) },
  { key: "item_f1", label: "Line-item F1", format: (p) => percent(p.item_f1) },
  { key: "cost_per_1000", label: "$ per 1,000", format: (p) => dollars(p.cost_per_1000) },
  { key: "median_seconds", label: "Median time", format: (p) => seconds(p.median_seconds) },
  { key: "catch_rate", label: "Errors caught", format: (p) => percent(p.catch_rate) },
];

function isEstimate(provider: Provider): boolean {
  return provider.cost_note.toLowerCase().startsWith("estimate");
}

function ProvisionalNote({ data }: { data: Leaderboard }) {
  return (
    <p className="mb-6 flex gap-2 rounded-control border border-border bg-surface px-4 py-3">
      <span className="mt-0.5 text-warn">
        <InfoIcon />
      </span>
      <span>
        <strong className="font-semibold">Provisional.</strong> These are {data.split} scores on the{" "}
        {data.receipts} receipts every provider has answered. Final scores will come from the
        held-out test split.
      </span>
    </p>
  );
}

function RoutingOrder({ data }: { data: Leaderboard }) {
  const byName = new Map(data.providers.map((provider) => [provider.provider, provider]));
  return (
    <section aria-labelledby="routing-order">
      <h2 id="routing-order" className="mb-3 text-base font-semibold">
        The order the router tries
      </h2>
      <ol className="divide-y divide-border border-y border-border">
        {data.routing_order.map((name, index) => {
          const provider = byName.get(name);
          const miss =
            provider && missesPolicy(provider, data.accuracy_target, data.max_seconds);
          return (
            <li key={name} className="flex gap-3 py-2.5">
              <span className="numeric w-4 text-text-2">{index + 1}</span>
              <span>
                <span className="block font-medium">{providerName(name)}</span>
                <span className="block text-[13px] text-text-2">
                  {miss ? `Last resort: ${miss}` : "Meets the policy"}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function ScoreTable({ data }: { data: Leaderboard }) {
  const [sortKey, setSortKey] = useState<SortKey>("fully_correct");
  const rows = sortProviders(data.providers, sortKey);
  return (
    <section aria-labelledby="scores">
      <h2 id="scores" className="mb-3 text-base font-semibold">
        Scores
      </h2>
      {/* Focusable, so it can be scrolled with the keyboard when it doesn't fit. */}
      <div
        tabIndex={0}
        role="region"
        aria-labelledby="scores"
        className="overflow-x-auto rounded-control border border-border"
      >
        <table className="w-full min-w-[720px] border-collapse">
          <thead className="bg-surface text-[13px] text-text-2">
            <tr>
              <th
                scope="col"
                className="sticky left-0 z-[1] bg-surface px-4 py-2 text-left font-medium"
              >
                Provider
              </th>
              {COLUMNS.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={sortKey === column.key ? "descending" : "none"}
                  className="px-4 py-1 text-right font-medium"
                >
                  <button
                    type="button"
                    onClick={() => setSortKey(column.key)}
                    className="group inline-flex min-h-8 items-center gap-1 rounded-badge whitespace-nowrap hover:text-text"
                  >
                    <SortIcon active={sortKey === column.key} />
                    {column.label}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((provider) => (
              <tr key={provider.provider} className="border-t border-border">
                <th
                  scope="row"
                  className="sticky left-0 z-[1] bg-bg px-4 py-3 text-left font-medium"
                >
                  <span className="block max-w-[170px] sm:max-w-none">
                    {providerName(provider.provider)}
                  </span>
                  {provider.local && (
                    <span className="block text-[13px] font-normal text-text-2">
                      Stays on this machine
                    </span>
                  )}
                </th>
                {COLUMNS.map((column) => (
                  <td key={column.key} className="numeric px-4 py-3 text-right whitespace-nowrap">
                    {column.key === "cost_per_1000" && isEstimate(provider) && (
                      <span className="mr-1.5 font-sans text-[13px] text-text-2">est.</span>
                    )}
                    {column.format(provider)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Notes({ data }: { data: Leaderboard }) {
  return (
    <section aria-labelledby="notes" className="max-w-[72ch]">
      <h2 id="notes" className="mb-3 text-base font-semibold">
        How to read this
      </h2>
      <dl className="grid gap-2 text-text-2">
        <div>
          <dt className="inline font-medium text-text">Fully correct: </dt>
          <dd className="inline">every line item and amount match the hand-made label.</dd>
        </div>
        <div>
          <dt className="inline font-medium text-text">Errors caught: </dt>
          <dd className="inline">
            of the receipts this provider got wrong, the share the arithmetic check flagged before
            they reached the database.
          </dd>
        </div>
        {data.providers.map((provider) => (
          <div key={provider.provider}>
            <dt className="inline font-medium text-text">
              {providerName(provider.provider)} cost:{" "}
            </dt>
            <dd className="inline">{provider.cost_note}.</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function LoadingLeaderboard() {
  return (
    <div role="status" aria-label="Loading scores" className="grid gap-6">
      <Skeleton className="h-12" />
      <Skeleton className="h-[340px]" />
      <Skeleton className="h-64" />
    </div>
  );
}

export function LeaderboardPage() {
  const leaderboard = useQuery({ queryKey: ["leaderboard"], queryFn: api.leaderboard });
  const data = leaderboard.data;
  const firstChoice = data?.routing_order[0];

  return (
    <>
      <PageHeading title="Model leaderboard">
        {data && firstChoice
          ? `The router tries ${providerName(firstChoice)} first: the cheapest provider reaching ${Math.round(data.accuracy_target * 100)}% fully correct and answering within ${data.max_seconds} s.`
          : "How accurate, fast and expensive each extraction provider is."}
      </PageHeading>
      {leaderboard.isPending && <LoadingLeaderboard />}
      {leaderboard.isError && !data && (
        <ErrorState
          title="Couldn't load the scores"
          error={leaderboard.error}
          onRetry={() => void leaderboard.refetch()}
        />
      )}
      {data && (
        <>
          <ProvisionalNote data={data} />
          {/* minmax(0, ...) so the wide table scrolls inside its box, not the whole page. */}
          <div className="grid grid-cols-[minmax(0,1fr)] gap-10">
            <div className="grid grid-cols-[minmax(0,1fr)] gap-10 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
              <Suspense fallback={<Skeleton className="h-[340px]" />}>
                <AccuracyCostChart providers={data.providers} target={data.accuracy_target} />
              </Suspense>
              <RoutingOrder data={data} />
            </div>
            <ScoreTable data={data} />
            <Notes data={data} />
          </div>
        </>
      )}
    </>
  );
}
