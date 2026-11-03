import type { Issue, Receipt } from "./api";

/** What the correction form edits: every value a string, exactly as typed. */
export interface ItemDraft {
  id: number;
  name: string;
  quantity: string;
  unit_price: string;
  total_price: string;
  discount: string;
}

export const AMOUNT_FIELDS = ["subtotal", "discount", "service_charge", "tax", "tip"] as const;
export type AmountField = (typeof AMOUNT_FIELDS)[number];

export interface ReceiptDraft {
  vendor: string;
  items: ItemDraft[];
  amounts: Record<AmountField, string>;
  total: string;
}

let nextId = 0;

export function blankItem(): ItemDraft {
  nextId += 1;
  return { id: nextId, name: "", quantity: "1", unit_price: "", total_price: "", discount: "" };
}

/** A receipt the model couldn't read starts with one empty line, ready to fill in. */
export function toDraft(receipt: Receipt | null): ReceiptDraft {
  const items = receipt?.items.map((item) => ({
    ...blankItem(),
    name: item.name,
    quantity: item.quantity,
    unit_price: item.unit_price ?? "",
    total_price: item.total_price,
    discount: item.discount ?? "",
  }));
  return {
    vendor: receipt?.vendor ?? "",
    items: items?.length ? items : [blankItem()],
    amounts: {
      subtotal: receipt?.subtotal ?? "",
      discount: receipt?.discount ?? "",
      service_charge: receipt?.service_charge ?? "",
      tax: receipt?.tax ?? "",
      tip: receipt?.tip ?? "",
    },
    total: receipt?.total ?? "",
  };
}

const orNull = (value: string): string | null => value.trim() || null;

/** The receipt to send. `currency` isn't editable, so it carries over from the model's answer. */
export function fromDraft(draft: ReceiptDraft, currency: string | null): Receipt {
  return {
    vendor: orNull(draft.vendor),
    currency,
    items: draft.items.map((item) => ({
      name: item.name.trim(),
      quantity: item.quantity.trim() || "1",
      unit_price: orNull(item.unit_price),
      total_price: item.total_price.trim(),
      discount: orNull(item.discount),
    })),
    subtotal: orNull(draft.amounts.subtotal),
    discount: orNull(draft.amounts.discount),
    service_charge: orNull(draft.amounts.service_charge),
    tax: orNull(draft.amounts.tax),
    tip: orNull(draft.amounts.tip),
    total: draft.total.trim(),
  };
}

export function issuesAt(issues: readonly Issue[], location: string): Issue[] {
  return issues.filter((issue) => issue.location === location);
}

/** The first problem with what's typed, or null. The server still has the final say. */
export function draftProblem(draft: ReceiptDraft): string | null {
  const isAmount = (value: string) => /^\d+(\.\d+)?$/.test(value.trim());
  const isOptionalAmount = (value: string) => value.trim() === "" || isAmount(value);

  for (const [index, item] of draft.items.entries()) {
    const line = `Line ${index + 1}`;
    if (!item.name.trim()) return `${line} needs a name.`;
    if (!isAmount(item.total_price)) return `${line} needs a total, as a number like 12500.`;
    if (!isOptionalAmount(item.quantity) || Number(item.quantity || 1) <= 0) {
      return `${line} needs a quantity above zero.`;
    }
    if (!isOptionalAmount(item.unit_price) || !isOptionalAmount(item.discount)) {
      return `${line} has an amount that isn't a number.`;
    }
  }
  if (!Object.values(draft.amounts).every(isOptionalAmount)) {
    return "One of the amounts isn't a number.";
  }
  if (!isAmount(draft.total)) return "The total is required, as a number like 12500.";
  return null;
}
