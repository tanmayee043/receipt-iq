import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, ApiError, demoImageUrl, type DemoSample, type Receipt } from "../api";
import { ReceiptEditor } from "../components/ReceiptEditor";
import { ReceiptImage } from "../components/ReceiptImage";
import { ReceiptView } from "../components/ReceiptView";
import { RoutePath } from "../components/RoutePath";
import { SplitBill } from "../components/SplitBill";
import { Button, ErrorState, PageHeading, Skeleton } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { providerName, seconds } from "../format";
import { Attribution, DEMO_SAMPLES_KEY, DemoNote } from "./DemoPage";

const REJECTED =
  "That correction doesn't add up. Confirm it if the receipt itself is misprinted.";

function SampleBody({ sample }: { sample: DemoSample }) {
  // A visitor's fix lives here only, and is gone when they leave the page.
  const [fixed, setFixed] = useState<Receipt | null>(null);
  const [editing, setEditing] = useState(sample.status === "needs_review");

  async function check(receipt: Receipt, confirmMismatch: boolean) {
    const result = await api.demoCheck(receipt);
    if (!result.passed && !confirmMismatch) {
      throw new ApiError(422, "correction_rejected", REJECTED, { issues: result.issues });
    }
    setFixed(receipt);
    setEditing(false);
  }

  const current = fixed ?? sample.receipt;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className="lg:sticky lg:top-4">
        <ReceiptImage src={demoImageUrl(sample.id)} />
      </div>
      <div className="grid min-w-0 gap-6">
        <div className="grid gap-1">
          <p className="text-base">
            <StatusBadge status={fixed ? "corrected" : sample.status} />
          </p>
          <div className="text-text-2">
            <RoutePath attempts={sample.attempts} />
            <p>
              Answered by {providerName(sample.provider)} in{" "}
              <span className="numeric">{seconds(sample.seconds)}</span>
            </p>
          </div>
        </div>
        {editing ? (
          <section className="min-w-0">
            <h2 className="mb-1 text-base font-semibold">Fix what the model got wrong</h2>
            <p className="mb-3 max-w-prose text-text-2">
              The arithmetic check runs on your fix too. Compare it with the image.
            </p>
            <ReceiptEditor
              receipt={current}
              issues={fixed ? [] : sample.issues}
              onSave={check}
            />
            {sample.status === "needs_review" && (
              <p className="mt-3 text-text-2">You can split the bill once it's fixed.</p>
            )}
          </section>
        ) : (
          <>
            <section className="grid min-w-0 gap-3">
              <h2 className="text-base font-semibold">{fixed ? "Your fix" : "What it read"}</h2>
              <ReceiptView receipt={current} />
              <div>
                <Button onClick={() => setEditing(true)}>Correct this</Button>
              </div>
            </section>
            <SplitBill
              key={JSON.stringify(current.items.map((item) => item.name))}
              receipt={current}
              storageKey={`demo-${sample.id}`}
            />
          </>
        )}
      </div>
    </div>
  );
}

export function DemoSamplePage() {
  const { sampleId = "" } = useParams();
  const samples = useQuery({ queryKey: DEMO_SAMPLES_KEY, queryFn: api.demoSamples });
  const sample = samples.data?.find((candidate) => candidate.id === sampleId);

  return (
    <>
      <PageHeading title={sample?.title ?? "Sample receipt"}>{sample?.note}</PageHeading>
      <DemoNote />
      {samples.isPending && (
        <div role="status" aria-label="Loading the sample" className="grid gap-6 lg:grid-cols-2">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      )}
      {samples.isError && !samples.data && (
        <ErrorState
          title="Couldn't load this sample"
          error={samples.error}
          onRetry={() => void samples.refetch()}
        />
      )}
      {samples.data && !sample && (
        <p>
          There's no sample with that name.{" "}
          <Link to="/demo" className="font-medium text-accent underline">
            See all the samples
          </Link>
        </p>
      )}
      {sample && <SampleBody key={sample.id} sample={sample} />}
      {sample && (
        <p className="mt-6">
          <Link to="/demo" className="font-medium text-accent underline">
            See the other samples
          </Link>
        </p>
      )}
      <Attribution />
    </>
  );
}
