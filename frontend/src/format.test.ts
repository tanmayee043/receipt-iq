import { describe, expect, it } from "vitest";

import {
  dollars,
  missesPolicy,
  outcomeWord,
  percent,
  providerName,
  seconds,
  sortProviders,
} from "./format";

describe("formatting", () => {
  it("shows a missing rate as a dash, not zero", () => {
    expect(percent(null)).toBe("–");
    expect(percent(0)).toBe("0.0%");
    expect(percent(0.8634)).toBe("86.3%");
  });

  it("shows money with two decimals", () => {
    expect(dollars(0)).toBe("$0.00");
    expect(dollars(5.4)).toBe("$5.40");
  });

  it("drops the decimal on long waits", () => {
    expect(seconds(8.31)).toBe("8.3 s");
    expect(seconds(111.2)).toBe("111 s");
    expect(seconds(null)).toBe("–");
  });

  it("falls back to the raw name for a provider it doesn't know", () => {
    expect(providerName("gpt")).toBe("GPT-5 mini");
    expect(providerName("new-one")).toBe("new-one");
  });

  it("uses plain words for routing outcomes", () => {
    expect(outcomeWord("needs_review")).toBe("flagged");
    expect(outcomeWord("something_new")).toBe("something_new");
  });
});

describe("missesPolicy", () => {
  it("says nothing for a provider that qualifies", () => {
    expect(missesPolicy({ fully_correct: 0.78, median_seconds: 8 }, 0.75, 60)).toBeNull();
  });

  it("counts exactly on the line as qualifying", () => {
    expect(missesPolicy({ fully_correct: 0.75, median_seconds: 60 }, 0.75, 60)).toBeNull();
  });

  it("names each thing the provider misses", () => {
    expect(missesPolicy({ fully_correct: 0.8, median_seconds: 111 }, 0.75, 60)).toBe(
      "slower than 60 s",
    );
    expect(missesPolicy({ fully_correct: 0.04, median_seconds: 111 }, 0.75, 60)).toBe(
      "below 75% fully correct, slower than 60 s",
    );
  });
});

describe("sortProviders", () => {
  const row = (provider: string, fully: number, cost: number, catchRate: number | null) => ({
    provider,
    fully_correct: fully,
    item_f1: fully,
    cost_per_1000: cost,
    median_seconds: cost,
    catch_rate: catchRate,
  });
  const rows = [row("a", 0.5, 1, null), row("b", 0.9, 5, 0.2), row("c", 0.7, 0, 0.8)];

  it("puts the most accurate first", () => {
    expect(sortProviders(rows, "fully_correct").map((r) => r.provider)).toEqual(["b", "c", "a"]);
  });

  it("puts the cheapest first", () => {
    expect(sortProviders(rows, "cost_per_1000").map((r) => r.provider)).toEqual(["c", "a", "b"]);
  });

  it("puts missing values last", () => {
    expect(sortProviders(rows, "catch_rate").map((r) => r.provider)).toEqual(["c", "b", "a"]);
  });

  it("doesn't change the list it was given", () => {
    sortProviders(rows, "cost_per_1000");
    expect(rows[0]?.provider).toBe("a");
  });
});
