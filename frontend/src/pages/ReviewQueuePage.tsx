import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, ApiError, imageUrl, type RoutedDocument } from "../api";
import { RequireLogin } from "../components/RequireLogin";
import { Button, EmptyState, ErrorState, PageHeading, Skeleton } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { dateTime, providerName } from "../format";

function reasonFor(document: RoutedDocument): string {
  const first = document.issues[0]?.message ?? document.failure_reason;
  if (!first) return "The total couldn't be checked against anything.";
  const more = document.issues.length - 1;
  return more > 0 ? `${first} (and ${more} more)` : first;
}

function QueueRow({ document }: { document: RoutedDocument }) {
  return (
    <li className="border-t border-border first:border-t-0">
      <Link
        to={`/receipts/${document.digest}`}
        className="flex gap-3 px-4 py-3 no-underline transition-colors duration-150 hover:bg-surface"
      >
        <img
          src={imageUrl(document.digest)}
          alt=""
          loading="lazy"
          className="h-16 w-12 shrink-0 rounded-badge border border-border bg-surface object-cover"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center justify-between gap-x-4">
            <StatusBadge status={document.status} />
            <span className="text-[13px] text-text-2">{dateTime(document.created_at)}</span>
          </span>
          <span className="mt-0.5 line-clamp-2 block [overflow-wrap:anywhere]">
            {reasonFor(document)}
          </span>
          {document.provider && (
            <span className="block text-[13px] text-text-2">
              {providerName(document.provider)}
            </span>
          )}
        </span>
      </Link>
    </li>
  );
}

function Queue() {
  const queue = useInfiniteQuery({
    queryKey: ["review-queue"],
    queryFn: ({ pageParam }) => api.reviewQueue(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    staleTime: 0,
  });

  if (queue.isPending) {
    return (
      <div role="status" aria-label="Loading the review queue" className="grid gap-2">
        {[0, 1, 2, 3].map((row) => (
          <Skeleton key={row} className="h-20" />
        ))}
      </div>
    );
  }
  if (queue.isError && !queue.data) {
    return (
      <ErrorState
        title="Couldn't load the review queue"
        error={queue.error}
        onRetry={() => void queue.refetch()}
      />
    );
  }
  const documents = queue.data?.pages.flatMap((page) => page.items) ?? [];
  if (documents.length === 0) {
    return (
      <EmptyState title="Nothing is waiting for review">
        A receipt lands here when its numbers don't add up or no provider could read it.
      </EmptyState>
    );
  }
  return (
    <>
      <ul className="overflow-hidden rounded-control border border-border">
        {documents.map((document) => (
          <QueueRow key={document.digest} document={document} />
        ))}
      </ul>
      {queue.isFetchNextPageError && (
        <p role="alert" className="mt-3 text-down">
          {queue.error instanceof ApiError ? queue.error.message : "Couldn't load more."}
        </p>
      )}
      {queue.hasNextPage && (
        <div className="mt-4">
          <Button disabled={queue.isFetchingNextPage} onClick={() => void queue.fetchNextPage()}>
            {queue.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}
    </>
  );
}

export function ReviewQueuePage() {
  return (
    <>
      <PageHeading title="Review queue">
        Receipts waiting for a person, oldest first. Each fix also becomes labeled data for
        measuring the providers.
      </PageHeading>
      <RequireLogin
        title="Log in to review receipts"
        why="Receipts are real uploads, so they're only open to the owner."
      >
        <Queue />
      </RequireLogin>
    </>
  );
}
