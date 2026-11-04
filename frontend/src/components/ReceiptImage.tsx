import { useState } from "react";

import { Button } from "./States";

export function ReceiptImage({ src }: { src: string }) {
  const [zoomed, setZoomed] = useState(false);
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <p className="rounded-control border border-border bg-surface px-4 py-8 text-text-2">
        Couldn't load the receipt image.
      </p>
    );
  }
  return (
    <figure>
      {/* Focusable, so a zoomed image can be scrolled with the keyboard. */}
      <div
        tabIndex={0}
        role="region"
        aria-label="Receipt image"
        className="max-h-[75vh] overflow-auto rounded-control border border-border bg-surface"
      >
        <img
          src={src}
          alt="The uploaded receipt"
          onError={() => setFailed(true)}
          className={zoomed ? "w-[220%] max-w-none" : "mx-auto w-full"}
        />
      </div>
      <figcaption className="mt-2">
        <Button aria-pressed={zoomed} onClick={() => setZoomed(!zoomed)}>
          {zoomed ? "Zoom out" : "Zoom in"}
        </Button>
      </figcaption>
    </figure>
  );
}
