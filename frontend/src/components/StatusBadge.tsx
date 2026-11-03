import type { ReactNode } from "react";

import { AlertIcon, CheckIcon, ClockIcon, CrossIcon, FlagIcon, PencilIcon } from "./Icons";

const STATUSES: Record<string, { label: string; icon: ReactNode; color: string }> = {
  passed: { label: "Passed", icon: <CheckIcon />, color: "text-up" },
  needs_review: { label: "Needs review", icon: <FlagIcon />, color: "text-warn" },
  dead_letter: { label: "Dead letter", icon: <AlertIcon />, color: "text-down" },
  failed: { label: "Failed", icon: <AlertIcon />, color: "text-down" },
  processing: { label: "Processing", icon: <ClockIcon />, color: "text-text-2" },
  corrected: { label: "Corrected", icon: <PencilIcon />, color: "text-accent" },
  dismissed: { label: "Dismissed", icon: <CrossIcon />, color: "text-text-2" },
};

/** The icon takes the status color; the word stays in text color, so it always reads. */
export function StatusBadge({ status }: { status: string }) {
  const known = STATUSES[status];
  return (
    <span className="inline-flex items-center gap-1.5 font-medium whitespace-nowrap">
      {known && <span className={known.color}>{known.icon}</span>}
      {known?.label ?? status}
    </span>
  );
}
