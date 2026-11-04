/**
 * Who owes what. Whole-number arithmetic throughout, so shares always add up to the total
 * printed on the receipt, to the last unit.
 *
 * - An item is shared equally among the people assigned to it, after its own discount.
 * - The whole receipt total is then shared in proportion to what each person's items cost.
 *   That spreads tax, service, tip and receipt discounts fairly, and also handles receipts
 *   whose prices already include tax, without needing to know which kind this one is.
 * - Items nobody has claimed yet get their own row, so the shares still add up meanwhile.
 */
import type { LineItem, Receipt } from "./api";

export interface Person {
  id: string;
  name: string;
}

/** For each item, by position, the ids of the people sharing it. */
export type Assignments = Record<number, string[]>;

export interface Share {
  items: string;
  /** Tax, service, tip and discounts. Negative when discounts outweigh the rest. */
  extras: string;
  total: string;
}

export interface SplitResult {
  shares: Record<string, Share>;
  unassigned: Share;
}

function decimalPlaces(value: string): number {
  return value.split(".")[1]?.length ?? 0;
}

function toUnits(value: string, scale: number): bigint {
  const [whole = "0", fraction = ""] = value.split(".");
  return BigInt(whole + fraction.padEnd(scale, "0"));
}

function fromUnits(units: bigint, scale: number): string {
  const negative = units < 0n;
  const digits = (negative ? -units : units).toString().padStart(scale + 1, "0");
  const whole = digits.slice(0, digits.length - scale);
  const fraction = scale > 0 ? `.${digits.slice(-scale)}` : "";
  return `${negative ? "-" : ""}${whole}${fraction}`;
}

/**
 * Divide `total` in proportion to `weights`, with nothing lost to rounding: leftover units
 * go to the largest remainders, and earlier people win ties so the result never changes.
 */
export function allocate(total: bigint, weights: readonly bigint[]): bigint[] {
  const sum = weights.reduce((a, b) => a + b, 0n);
  if (sum === 0n) return weights.map(() => 0n);
  const parts = weights.map((weight) => ({
    amount: (total * weight) / sum,
    remainder: (total * weight) % sum,
  }));
  let left = total - parts.reduce((a, part) => a + part.amount, 0n);
  const order = parts
    .map((part, index) => ({ index, remainder: part.remainder }))
    .sort((a, b) =>
      a.remainder === b.remainder ? a.index - b.index : a.remainder > b.remainder ? -1 : 1,
    );
  for (const { index } of order) {
    if (left <= 0n) break;
    const part = parts[index];
    if (part) part.amount += 1n;
    left -= 1n;
  }
  return parts.map((part) => part.amount);
}

function netUnits(item: LineItem, scale: number): bigint {
  const net = toUnits(item.total_price, scale) - toUnits(item.discount ?? "0", scale);
  return net > 0n ? net : 0n;
}

export function splitBill(
  receipt: Receipt,
  people: readonly Person[],
  assignments: Assignments,
): SplitResult {
  const amounts = [
    receipt.total,
    ...receipt.items.flatMap((item) => [item.total_price, item.discount ?? "0"]),
  ];
  const scale = Math.max(...amounts.map(decimalPlaces));
  const known = new Set(people.map((person) => person.id));
  const itemUnits = new Map(people.map((person) => [person.id, 0n]));
  let unassignedItems = 0n;

  receipt.items.forEach((item, index) => {
    const sharers = (assignments[index] ?? []).filter((id) => known.has(id));
    const net = netUnits(item, scale);
    if (sharers.length === 0) {
      unassignedItems += net;
      return;
    }
    const parts = allocate(
      net,
      sharers.map(() => 1n),
    );
    sharers.forEach((id, position) => {
      itemUnits.set(id, (itemUnits.get(id) ?? 0n) + (parts[position] ?? 0n));
    });
  });

  const total = toUnits(receipt.total, scale);
  const personItems = people.map((person) => itemUnits.get(person.id) ?? 0n);
  const hasItems = personItems.some((units) => units > 0n) || unassignedItems > 0n;
  // With no prices to go on, such as a receipt with only a total, share it equally.
  const weights = hasItems ? [...personItems, unassignedItems] : [...people.map(() => 1n), 0n];
  const totals = people.length > 0 || hasItems ? allocate(total, weights) : [total];

  const share = (items: bigint, units: bigint): Share => ({
    items: fromUnits(items, scale),
    extras: fromUnits(units - items, scale),
    total: fromUnits(units, scale),
  });
  const unassignedTotal = people.length === 0 && !hasItems ? total : (totals.at(-1) ?? 0n);
  return {
    shares: Object.fromEntries(
      people.map((person, index) => [
        person.id,
        share(personItems[index] ?? 0n, totals[index] ?? 0n),
      ]),
    ),
    unassigned: share(unassignedItems, unassignedTotal),
  };
}

/** 123000.50 as 123,000.50, for reading. Doesn't convert to a number, so stays exact. */
export function grouped(value: string): string {
  const negative = value.startsWith("-");
  const [whole = "0", fraction] = (negative ? value.slice(1) : value).split(".");
  const withCommas = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${withCommas}${fraction ? `.${fraction}` : ""}`;
}
