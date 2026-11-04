import { describe, expect, it } from "vitest";

import type { LineItem, Receipt } from "./api";
import { allocate, grouped, splitBill } from "./split";

const item = (total_price: string, discount: string | null = null): LineItem => ({
  name: "ITEM",
  quantity: "1",
  unit_price: null,
  total_price,
  discount,
});

const receipt = (items: LineItem[], total: string): Receipt => ({
  vendor: null,
  currency: null,
  items,
  subtotal: null,
  discount: null,
  service_charge: null,
  tax: null,
  tip: null,
  total,
});

const ANA = { id: "a", name: "Ana" };
const BEN = { id: "b", name: "Ben" };
const CAL = { id: "c", name: "Cal" };

function sum(values: string[]): bigint {
  const scale = Math.max(...values.map((v) => v.split(".")[1]?.length ?? 0));
  return values.reduce((total, value) => {
    const negative = value.startsWith("-");
    const [whole = "0", fraction = ""] = value.replace("-", "").split(".");
    const units = BigInt(whole + fraction.padEnd(scale, "0"));
    return total + (negative ? -units : units);
  }, 0n);
}

describe("allocate", () => {
  it("loses nothing to rounding", () => {
    expect(allocate(100n, [1n, 1n, 1n])).toEqual([34n, 33n, 33n]);
  });

  it("gives leftovers to the largest remainder", () => {
    expect(allocate(10n, [1n, 2n])).toEqual([3n, 7n]);
  });

  it("gives nothing when no one has any weight", () => {
    expect(allocate(10n, [0n, 0n])).toEqual([0n, 0n]);
  });
});

describe("splitBill", () => {
  it("gives each person their own items", () => {
    const result = splitBill(receipt([item("1000"), item("3000")], "4000"), [ANA, BEN], {
      0: ["a"],
      1: ["b"],
    });
    expect(result.shares.a?.total).toBe("1000");
    expect(result.shares.b?.total).toBe("3000");
    expect(result.unassigned.total).toBe("0");
  });

  it("shares tax and tip in proportion to what each person had", () => {
    const result = splitBill(receipt([item("1000"), item("3000")], "4400"), [ANA, BEN], {
      0: ["a"],
      1: ["b"],
    });
    expect(result.shares.a).toEqual({ items: "1000", extras: "100", total: "1100" });
    expect(result.shares.b).toEqual({ items: "3000", extras: "300", total: "3300" });
  });

  it("shows a receipt discount as negative extras", () => {
    const result = splitBill(receipt([item("1000")], "900"), [ANA], { 0: ["a"] });
    expect(result.shares.a).toEqual({ items: "1000", extras: "-100", total: "900" });
  });

  it("takes a line's own discount off that line", () => {
    const result = splitBill(receipt([item("1000", "400"), item("400")], "1000"), [ANA, BEN], {
      0: ["a"],
      1: ["b"],
    });
    expect(result.shares.a?.items).toBe("600");
    expect(result.shares.b?.items).toBe("400");
  });

  it("works when prices already include tax", () => {
    const result = splitBill(receipt([item("5000"), item("5000")], "10000"), [ANA, BEN], {
      0: ["a"],
      1: ["b"],
    });
    expect(result.shares.a?.extras).toBe("0");
  });

  it("splits a shared item, and the shares still add up exactly", () => {
    const result = splitBill(receipt([item("100")], "100"), [ANA, BEN, CAL], {
      0: ["a", "b", "c"],
    });
    expect(result.shares.a?.total).toBe("34");
    expect(result.shares.b?.total).toBe("33");
    expect(result.shares.c?.total).toBe("33");
  });

  it("keeps cents, without floating point drift", () => {
    const result = splitBill(receipt([item("0.10"), item("0.20")], "0.30"), [ANA, BEN], {
      0: ["a"],
      1: ["b"],
    });
    expect(result.shares.a?.total).toBe("0.10");
    expect(result.shares.b?.total).toBe("0.20");
  });

  it("handles amounts too large for a JavaScript number", () => {
    const result = splitBill(receipt([item("9007199254740993")], "9007199254740993"), [ANA], {
      0: ["a"],
    });
    expect(result.shares.a?.total).toBe("9007199254740993");
  });

  it("puts unclaimed items in their own row", () => {
    const result = splitBill(receipt([item("1000"), item("3000")], "4400"), [ANA, BEN], {
      0: ["a"],
    });
    expect(result.shares.a?.total).toBe("1100");
    expect(result.shares.b?.total).toBe("0");
    expect(result.unassigned).toEqual({ items: "3000", extras: "300", total: "3300" });
  });

  it("forgets a person who was removed", () => {
    const result = splitBill(receipt([item("1000")], "1000"), [BEN], { 0: ["a"] });
    expect(result.unassigned.total).toBe("1000");
    expect(result.shares.a).toBeUndefined();
  });

  it("shares a receipt with only a total equally", () => {
    const result = splitBill(receipt([], "100"), [ANA, BEN, CAL], {});
    expect(result.shares.a?.total).toBe("34");
    expect(result.unassigned.total).toBe("0");
  });

  it("copes with no people at all", () => {
    expect(splitBill(receipt([], "100"), [], {}).unassigned.total).toBe("100");
    expect(splitBill(receipt([item("100")], "100"), [], {}).unassigned.total).toBe("100");
  });

  it("copes with free items and a zero total", () => {
    const result = splitBill(receipt([item("0")], "0"), [ANA], { 0: ["a"] });
    expect(result.shares.a?.total).toBe("0");
  });

  it("always adds up to the printed total, across many combinations", () => {
    const people = [ANA, BEN, CAL];
    for (let seed = 1; seed <= 300; seed += 1) {
      const pick = (limit: number, salt: number) => (seed * 7919 + salt * 104729) % limit;
      const count = 1 + pick(6, 1);
      const items = Array.from({ length: count }, (_, i) => item(String(pick(99991, i + 2))));
      const total = String(pick(999983, 50));
      const assignments = Object.fromEntries(
        items.map((_, i) => [i, people.filter((__, p) => pick(5, i * 10 + p) > 1).map((x) => x.id)]),
      );
      const result = splitBill(receipt(items, total), people, assignments);
      const shares = [...Object.values(result.shares), result.unassigned];
      expect(sum(shares.map((share) => share.total))).toBe(BigInt(total));
    }
  });
});

describe("grouped", () => {
  it("adds thousands separators without touching the digits", () => {
    expect(grouped("123000")).toBe("123,000");
    expect(grouped("1234567.50")).toBe("1,234,567.50");
    expect(grouped("999")).toBe("999");
    expect(grouped("-1000")).toBe("-1,000");
    expect(grouped("9007199254740993")).toBe("9,007,199,254,740,993");
  });
});
