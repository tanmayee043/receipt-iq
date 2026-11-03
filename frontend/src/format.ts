const PROVIDER_NAMES: Record<string, string> = {
  local: "Local (Tesseract + phi3)",
  gemini: "Gemini 3.5 Flash",
  gemma: "Gemma 4 31B",
  azure: "Azure Document Intelligence",
  gpt: "GPT-5 mini",
};

const SHORT_NAMES: Record<string, string> = {
  local: "Local",
  gemini: "Gemini",
  gemma: "Gemma",
  azure: "Azure",
  gpt: "GPT",
};

export function providerName(provider: string): string {
  return PROVIDER_NAMES[provider] ?? provider;
}

export function shortName(provider: string): string {
  return SHORT_NAMES[provider] ?? provider;
}

export function percent(value: number | null): string {
  return value === null ? "–" : `${(value * 100).toFixed(1)}%`;
}

export function dollars(value: number): string {
  return `$${value.toFixed(2)}`;
}

export function seconds(value: number | null): string {
  if (value === null) return "–";
  return value >= 100 ? `${Math.round(value)} s` : `${value.toFixed(1)} s`;
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const OUTCOMES: Record<string, string> = {
  passed: "passed",
  needs_review: "flagged",
  failed: "couldn't read it",
  unavailable: "unavailable",
  skipped: "skipped",
};

export function outcomeWord(outcome: string): string {
  return OUTCOMES[outcome] ?? outcome;
}

/** Why a provider isn't one the router tries first, or null if it qualifies. */
export function missesPolicy(
  provider: { fully_correct: number; median_seconds: number },
  target: number,
  maxSeconds: number,
): string | null {
  const reasons: string[] = [];
  if (provider.fully_correct < target) {
    reasons.push(`below ${Math.round(target * 100)}% fully correct`);
  }
  if (provider.median_seconds > maxSeconds) {
    reasons.push(`slower than ${maxSeconds} s`);
  }
  return reasons.length > 0 ? reasons.join(", ") : null;
}

export type SortKey =
  | "fully_correct"
  | "item_f1"
  | "cost_per_1000"
  | "median_seconds"
  | "catch_rate";

/** Best first: higher accuracy and catch rate, lower cost and time. Missing values go last. */
export function sortProviders<
  T extends Record<SortKey, number | null> & { provider: string },
>(providers: readonly T[], key: SortKey): T[] {
  const lowerIsBetter = key === "cost_per_1000" || key === "median_seconds";
  return [...providers].sort((a, b) => {
    const left = a[key];
    const right = b[key];
    if (left === null) return right === null ? 0 : 1;
    if (right === null) return -1;
    return (lowerIsBetter ? left - right : right - left) || a.provider.localeCompare(b.provider);
  });
}
