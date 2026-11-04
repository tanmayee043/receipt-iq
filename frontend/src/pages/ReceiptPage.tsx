import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link, useParams } from "react-router";

import { api, ApiError, imageUrl, type Receipt as ReceiptData, type RoutedDocument } from "../api";
import { ReceiptEditor } from "../components/ReceiptEditor";
import { ReceiptImage } from "../components/ReceiptImage";
import { ReceiptView } from "../components/ReceiptView";
import { RequireLogin } from "../components/RequireLogin";
import { RoutePath } from "../components/RoutePath";
import { SplitBill } from "../components/SplitBill";
import { Button, ErrorState, PageHeading, Skeleton } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { providerName, seconds } from "../format";

const POLL_MS = 2000;

function DismissForm({
  digest,
  onSaved,
}: {
  digest: string;
  onSaved: (saved: RoutedDocument) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const fieldId = useId();
  const dismiss = useMutation({
    mutationFn: () => api.dismiss(digest, reason.trim()),
    onSuccess: onSaved,
  });

  if (!open) return <Button onClick={() => setOpen(true)}>Dismiss this receipt</Button>;
  return (
    <form
      className="grid gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason.trim()) dismiss.mutate();
      }}
    >
      <label htmlFor={fieldId} className="text-[13px] font-medium text-text-2">
        Why is there nothing to extract?
      </label>
      <input
        id={fieldId}
        autoFocus
        maxLength={500}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        className="min-h-10 w-full rounded-control border border-border bg-raised px-3 text-base sm:text-sm"
      />
      {dismiss.isError && (
        <p role="alert" className="text-down">
          {dismiss.error.message}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={!reason.trim() || dismiss.isPending}>
          Dismiss
        </Button>
        <Button onClick={() => setOpen(false)}>Keep it</Button>
      </div>
    </form>
  );
}

function RetryButton({
  digest,
  onSaved,
}: {
  digest: string;
  onSaved: (saved: RoutedDocument) => void;
}) {
  const retry = useMutation({ mutationFn: () => api.retry(digest), onSuccess: onSaved });
  return (
    <div>
      <Button disabled={retry.isPending} onClick={() => retry.mutate()}>
        {retry.isPending ? "Starting…" : "Try extraction again"}
      </Button>
      {retry.isError && (
        <p role="alert" className="mt-2 text-down">
          {retry.error.message}
        </p>
      )}
    </div>
  );
}

function Summary({ document }: { document: RoutedDocument }) {
  return (
    <div className="mb-5 grid gap-1">
      <p className="text-base">
        <StatusBadge status={document.status} />
      </p>
      {document.provider && document.attempts.length > 0 && (
        <div className="text-text-2">
          <RoutePath attempts={document.attempts} />
          <p>
            Answered by {providerName(document.provider)}
            {document.seconds !== null && (
              <>
                {" in "}
                <span className="numeric">{seconds(document.seconds)}</span>
              </>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

function Panel({ document, onSaved }: { document: RoutedDocument; onSaved: (saved: RoutedDocument) => void }) {
  const [editing, setEditing] = useState(false);
  const saveCorrection = async (receipt: ReceiptData, confirmMismatch: boolean) => {
    onSaved(await api.correct(document.digest, receipt, confirmMismatch));
  };
  const editor = (
    <ReceiptEditor receipt={document.receipt} issues={document.issues} onSave={saveCorrection} />
  );
  const toQueue = (
    <Link to="/review" className="font-medium text-accent underline">
      Back to the review queue
    </Link>
  );

  switch (document.status) {
    case "processing":
      return (
        <div role="status" className="rounded-control border border-border bg-surface px-5 py-8">
          <p className="text-base font-semibold">Reading this receipt…</p>
          <p className="mt-1 text-text-2">
            This updates by itself. If providers are busy it can take a while, and it's safe to
            leave this page.
          </p>
        </div>
      );
    case "failed":
      return (
        <div className="grid gap-3 rounded-control border border-border bg-surface px-5 py-6">
          <p className="text-base font-semibold">Extraction couldn't run</p>
          <p className="text-text-2 [overflow-wrap:anywhere]">{document.failure_reason}</p>
          <RetryButton digest={document.digest} onSaved={onSaved} />
        </div>
      );
    case "corrected":
      return (
        <div className="grid gap-4">
          <section>
            <h2 className="mb-2 text-base font-semibold">Corrected answer</h2>
            {document.confirmed_mismatch && (
              <p className="mb-2 text-text-2">
                Saved as confirmed: it doesn't add up, because the receipt itself is misprinted.
              </p>
            )}
            {document.corrected_receipt && <ReceiptView receipt={document.corrected_receipt} />}
          </section>
          {document.receipt && (
            <details>
              <summary className="min-h-8 cursor-pointer font-medium">The model's answer</summary>
              <div className="mt-2">
                <ReceiptView receipt={document.receipt} />
              </div>
            </details>
          )}
          {document.corrected_receipt && (
            <SplitBill receipt={document.corrected_receipt} storageKey={document.digest} />
          )}
          <p>{toQueue}</p>
        </div>
      );
    case "dismissed":
      return (
        <div className="grid gap-3">
          <p>
            <span className="font-medium">Dismissed: </span>
            <span className="[overflow-wrap:anywhere]">{document.failure_reason}</span>
          </p>
          <p>{toQueue}</p>
        </div>
      );
    case "passed":
      return editing ? (
        editor
      ) : (
        <div className="grid gap-4">
          <ReceiptView receipt={document.receipt ?? emptyReceipt()} />
          <p className="text-text-2">
            The numbers add up, but that isn't proof: a wrong amount can still add up.
          </p>
          <div>
            <Button onClick={() => setEditing(true)}>Correct this</Button>
          </div>
          {document.receipt && (
            <SplitBill receipt={document.receipt} storageKey={document.digest} />
          )}
        </div>
      );
    default:
      // Needs review or dead letter: this is what a person came here to do.
      return (
        <div className="grid gap-5">
          {document.status === "dead_letter" && (
            <div className="grid gap-3 rounded-control border border-border bg-surface px-4 py-4">
              <p className="[overflow-wrap:anywhere]">
                <span className="font-medium">No provider could answer. </span>
                <span className="text-text-2">{document.failure_reason}</span>
              </p>
              <RetryButton digest={document.digest} onSaved={onSaved} />
            </div>
          )}
          {editor}
          <div>
            <DismissForm digest={document.digest} onSaved={onSaved} />
          </div>
        </div>
      );
  }
}

function emptyReceipt() {
  return {
    vendor: null,
    currency: null,
    items: [],
    subtotal: null,
    discount: null,
    service_charge: null,
    tax: null,
    tip: null,
    total: "0",
  };
}

function Receipt({ digest }: { digest: string }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["document", digest],
    queryFn: () => api.document(digest),
    staleTime: 0,
    refetchInterval: (current) => (current.state.data?.status === "processing" ? POLL_MS : false),
  });

  function saved(document: RoutedDocument) {
    queryClient.setQueryData(["document", digest], document);
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    void queryClient.invalidateQueries({ queryKey: ["routing-log"] });
  }

  if (query.isPending) {
    return (
      <div role="status" aria-label="Loading the receipt" className="grid gap-6 lg:grid-cols-2">
        <Skeleton className="h-96" />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (query.isError && !query.data) {
    const missing = query.error instanceof ApiError && query.error.status === 404;
    return (
      <ErrorState
        title={missing ? "There's no receipt here" : "Couldn't load this receipt"}
        error={query.error}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const document = query.data;
  if (!document) return null;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className="lg:sticky lg:top-4">
        <ReceiptImage src={imageUrl(digest)} />
      </div>
      <div className="min-w-0">
        <Summary document={document} />
        {/* A new run or a saved fix starts from fresh values rather than an old draft. */}
        <Panel key={`${document.status}-${document.updated_at}`} document={document} onSaved={saved} />
      </div>
    </div>
  );
}

export function ReceiptPage() {
  const { digest = "" } = useParams();
  return (
    <>
      <PageHeading title="Receipt" />
      <RequireLogin
        title="Log in to see this receipt"
        why="Receipts are real uploads, so they're only open to the owner."
      >
        <Receipt digest={digest} />
      </RequireLogin>
    </>
  );
}
