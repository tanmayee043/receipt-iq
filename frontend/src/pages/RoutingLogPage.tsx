import { useInfiniteQuery } from "@tanstack/react-query";
import { Fragment, useId, useState } from "react";

import { api, ApiError, type RoutedDocument } from "../api";
import { ArrowIcon, ChevronIcon } from "../components/Icons";
import { LoginForm } from "../components/LoginForm";
import { Button, EmptyState, ErrorState, PageHeading, Skeleton } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { dateTime, outcomeWord, providerName, seconds, shortName } from "../format";
import { useLoggedIn } from "../session";

const ROW_GRID = "sm:grid-cols-[1.25rem_7.5rem_7.5rem_8rem_minmax(0,1fr)_3.75rem]";

function RoutePath({ document }: { document: RoutedDocument }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
      {document.attempts.map((attempt, index) => (
        <Fragment key={`${attempt.provider}-${index}`}>
          {index > 0 && (
            <li aria-hidden="true" className="text-text-2">
              <ArrowIcon />
            </li>
          )}
          <li>
            {shortName(attempt.provider)}{" "}
            <span className="text-text-2">{outcomeWord(attempt.outcome)}</span>
          </li>
        </Fragment>
      ))}
    </ol>
  );
}

function Details({ document, id }: { document: RoutedDocument; id: string }) {
  return (
    <div id={id} className="grid gap-4 border-t border-border bg-surface px-4 py-4 sm:pl-12">
      <div>
        <h3 className="text-[13px] font-medium text-text-2">Receipt hash</h3>
        <p className="numeric text-[13px] break-all">{document.digest}</p>
      </div>
      <div>
        <h3 className="text-[13px] font-medium text-text-2">Each provider tried</h3>
        <ul className="mt-1 grid gap-1.5">
          {document.attempts.map((attempt, index) => (
            <li key={`${attempt.provider}-${index}`}>
              <span className="font-medium">{providerName(attempt.provider)}</span>{" "}
              {outcomeWord(attempt.outcome)}{" "}
              <span className="numeric text-text-2">in {seconds(attempt.seconds)}</span>
              {attempt.reason && (
                <span className="block text-text-2 [overflow-wrap:anywhere]">{attempt.reason}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
      {document.issues.length > 0 && (
        <div>
          <h3 className="text-[13px] font-medium text-text-2">Why it was flagged</h3>
          <ul className="mt-1 grid gap-1">
            {document.issues.map((issue, index) => (
              <li key={`${issue.code}-${index}`} className="[overflow-wrap:anywhere]">
                {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {document.failure_reason && (
        <div>
          <h3 className="text-[13px] font-medium text-text-2">What went wrong</h3>
          <p className="[overflow-wrap:anywhere]">{document.failure_reason}</p>
        </div>
      )}
      {document.met_target === false && (
        <p className="text-text-2">
          Only a provider missing the accuracy target or speed limit could answer this one.
        </p>
      )}
    </div>
  );
}

function Row({ document }: { document: RoutedDocument }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  return (
    <li className="border-t border-border first:border-t-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? detailsId : undefined}
        onClick={() => setOpen(!open)}
        className={`grid w-full grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1 px-4 py-3 text-left transition-colors duration-150 hover:bg-surface sm:items-center ${ROW_GRID}`}
      >
        <span className="text-text-2">
          <ChevronIcon open={open} />
        </span>
        <span className="sm:order-3">
          <StatusBadge status={document.status} />
        </span>
        <span className="numeric text-right text-text-2 sm:order-6">
          {seconds(document.seconds)}
        </span>
        <span className="col-start-2 col-span-2 text-text-2 sm:order-1 sm:col-span-1 sm:col-start-auto">
          {dateTime(document.created_at)}
        </span>
        <span className="numeric col-start-2 col-span-2 text-text-2 sm:order-2 sm:col-span-1 sm:col-start-auto">
          <span className="sr-only">Receipt </span>
          {document.digest.slice(0, 10)}
        </span>
        <span className="col-start-2 col-span-2 sm:order-5 sm:col-span-1 sm:col-start-auto">
          <RoutePath document={document} />
        </span>
      </button>
      {open && <Details document={document} id={detailsId} />}
    </li>
  );
}

function ColumnHeadings() {
  return (
    <div
      aria-hidden="true"
      className={`hidden gap-x-3 bg-surface px-4 py-2 text-[13px] font-medium text-text-2 sm:grid ${ROW_GRID}`}
    >
      <span />
      <span>Received</span>
      <span>Receipt</span>
      <span>Result</span>
      <span>Route</span>
      <span className="text-right">Time</span>
    </div>
  );
}

function LoadingRows() {
  return (
    <div role="status" aria-label="Loading the routing log" className="grid gap-2">
      {[0, 1, 2, 3, 4].map((row) => (
        <Skeleton key={row} className="h-12" />
      ))}
    </div>
  );
}

function RoutingLog() {
  const log = useInfiniteQuery({
    queryKey: ["routing-log"],
    queryFn: ({ pageParam }) => api.routingLog(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });

  if (log.isPending) return <LoadingRows />;
  if (log.isError && !log.data) {
    return (
      <ErrorState
        title="Couldn't load the routing log"
        error={log.error}
        onRetry={() => void log.refetch()}
      />
    );
  }

  const documents = log.data?.pages.flatMap((page) => page.items) ?? [];
  if (documents.length === 0) {
    return (
      <EmptyState title="No receipts routed yet">
        Each receipt the router handles appears here, with every provider it tried and why.
      </EmptyState>
    );
  }
  return (
    <>
      <div className="overflow-hidden rounded-control border border-border">
        <ColumnHeadings />
        <ul>
          {documents.map((document) => (
            <Row key={document.digest} document={document} />
          ))}
        </ul>
      </div>
      {log.isFetchNextPageError && (
        <p role="alert" className="mt-3 text-down">
          {log.error instanceof ApiError ? log.error.message : "Couldn't load more."}
        </p>
      )}
      {log.hasNextPage && (
        <div className="mt-4">
          <Button disabled={log.isFetchingNextPage} onClick={() => void log.fetchNextPage()}>
            {log.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}
    </>
  );
}

export function RoutingLogPage() {
  const loggedIn = useLoggedIn();
  return (
    <>
      <PageHeading title="Routing log">
        Which provider handled each receipt and why, newest first. A row shows the latest run of
        that image.
      </PageHeading>
      {loggedIn === undefined && <LoadingRows />}
      {loggedIn === false && (
        <section className="rounded-control border border-border bg-surface px-5 py-6">
          <h2 className="text-base font-semibold">Log in to see the routing log</h2>
          <p className="mt-1 mb-4 max-w-prose text-text-2">
            It lists real uploads, so it's only open to the owner. The leaderboard is public.
          </p>
          <LoginForm />
        </section>
      )}
      {loggedIn && <RoutingLog />}
    </>
  );
}
