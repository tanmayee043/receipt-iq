import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type DragEvent, useState } from "react";
import { useNavigate } from "react-router";

import { api, ApiError } from "../api";
import { RequireLogin } from "../components/RequireLogin";
import { PageHeading } from "../components/States";

const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED = ["image/jpeg", "image/png"];

/** Saves a wasted upload. The server checks the file itself again, whatever this says. */
function problemWith(file: File): string | null {
  if (!ALLOWED.includes(file.type)) return "That isn't a JPEG or PNG image.";
  if (file.size > MAX_BYTES) return "That image is over the 10 MB limit.";
  return null;
}

function UploadForm() {
  const [localOnly, setLocalOnly] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const upload = useMutation({
    mutationFn: (file: File) => api.upload(file, localOnly),
    onSuccess: (document) => {
      queryClient.setQueryData(["document", document.digest], document);
      void queryClient.invalidateQueries({ queryKey: ["routing-log"] });
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      void navigate(`/receipts/${document.digest}`);
    },
    onError: (error) => {
      setProblem(error instanceof ApiError ? error.message : "That upload failed.");
    },
  });

  function choose(file: File | undefined) {
    if (!file || upload.isPending) return;
    const found = problemWith(file);
    setProblem(found);
    if (!found) upload.mutate(file);
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  return (
    <div className="grid max-w-xl gap-4">
      <label
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
        className={`block cursor-pointer rounded-control border-2 border-dashed px-6 py-12 text-center transition-colors duration-150 has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-accent ${
          dragging ? "border-accent bg-accent-subtle" : "border-border bg-surface hover:border-text-2"
        }`}
      >
        <span className="block text-base font-semibold">
          {upload.isPending ? "Uploading…" : "Choose or take a photo of a receipt"}
        </span>
        <span className="mt-1 block text-text-2">Or drop it here. JPEG or PNG, up to 10 MB.</span>
        <input
          type="file"
          accept="image/jpeg,image/png"
          disabled={upload.isPending}
          className="sr-only"
          onChange={(event) => {
            choose(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
      </label>
      {problem && (
        <p role="alert" className="text-down">
          {problem}
        </p>
      )}
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={localOnly}
          onChange={(event) => setLocalOnly(event.target.checked)}
          className="mt-1 size-4 accent-[var(--accent)]"
        />
        <span>
          <span className="block font-medium">Keep this on this machine</span>
          <span className="block text-text-2">
            For sensitive documents. Nothing goes to a cloud provider, but the local model is far
            less accurate, so expect to correct it.
          </span>
        </span>
      </label>
    </div>
  );
}

export function UploadPage() {
  return (
    <>
      <PageHeading title="Upload a receipt">
        The router picks a provider, and the arithmetic check decides whether a person should look
        at the answer.
      </PageHeading>
      <RequireLogin
        title="Log in to upload"
        why="Each upload spends provider quota, so it's only open to the owner."
      >
        <UploadForm />
      </RequireLogin>
    </>
  );
}
