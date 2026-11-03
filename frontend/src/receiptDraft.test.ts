import { describe, expect, it } from "vitest";

import type { Receipt } from "./api";
import { blankItem, draftProblem, fromDraft, issuesAt, toDraft } from "./receiptDraft";

const RECEIPT: Receipt = {
  vendor: "Warung",
  currency: "IDR",
  items: [
    { name: "TEA", quantity: "2", unit_price: "2500", total_price: "5000", discount: null },
  ],
  subtotal: "5000",
  discount: null,
  service_charge: null,
  tax: "500",
  tip: null,
  total: "5500",
};

describe("drafts", () => {
  it("round-trips a receipt without changing any value", () => {
    expect(fromDraft(toDraft(RECEIPT), "IDR")).toEqual(RECEIPT);
  });

  it("keeps long amounts exact, because they stay strings", () => {
    const big = { ...RECEIPT, total: "12345678901234567890.12" };
    expect(fromDraft(toDraft(big), "IDR").total).toBe("12345678901234567890.12");
  });

  it("starts an unreadable receipt with one empty line", () => {
    const draft = toDraft(null);
    expect(draft.items).toHaveLength(1);
    expect(draft.total).toBe("");
  });

  it("sends blanks as null and a blank quantity as 1", () => {
    const draft = toDraft(RECEIPT);
    const item = draft.items[0];
    if (!item) throw new Error("no item");
    item.quantity = " ";
    item.unit_price = " ";
    draft.amounts.tax = "";
    draft.vendor = "  ";
    const receipt = fromDraft(draft, null);
    expect(receipt.items[0]?.quantity).toBe("1");
    expect(receipt.items[0]?.unit_price).toBeNull();
    expect(receipt.tax).toBeNull();
    expect(receipt.vendor).toBeNull();
  });

  it("gives each line its own id, so removing one can't edit another", () => {
    expect(blankItem().id).not.toBe(blankItem().id);
  });
});

describe("draftProblem", () => {
  it("accepts a complete receipt", () => {
    expect(draftProblem(toDraft(RECEIPT))).toBeNull();
  });

  it("names the line that's missing something", () => {
    const draft = toDraft(RECEIPT);
    draft.items.push(blankItem());
    expect(draftProblem(draft)).toBe("Line 2 needs a name.");
    const added = draft.items[1];
    if (!added) throw new Error("no item");
    added.name = "RICE";
    expect(draftProblem(draft)).toContain("Line 2 needs a total");
  });

  it.each(["abc", "-5", "1,000", "1e5"])("refuses %s as an amount", (value) => {
    const draft = toDraft(RECEIPT);
    draft.total = value;
    expect(draftProblem(draft)).toContain("total is required");
  });

  it("refuses a zero quantity and a bad optional amount", () => {
    const draft = toDraft(RECEIPT);
    const item = draft.items[0];
    if (!item) throw new Error("no item");
    item.quantity = "0";
    expect(draftProblem(draft)).toContain("quantity above zero");
    item.quantity = "1";
    draft.amounts.tip = "lots";
    expect(draftProblem(draft)).toContain("isn't a number");
  });
});

describe("issuesAt", () => {
  it("finds the issues for one part of the receipt", () => {
    const issue = { code: "c", message: "m", expected: "1", actual: "2", location: "items[1]" };
    expect(issuesAt([issue], "items[1]")).toEqual([issue]);
    expect(issuesAt([issue], "items[0]")).toEqual([]);
  });
});
