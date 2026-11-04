import { useEffect, useId, useState } from "react";

import type { Receipt } from "../api";
import { type Assignments, grouped, type Person, splitBill } from "../split";
import { AlertIcon, CheckIcon } from "./Icons";
import { Button } from "./States";

interface Saved {
  people: Person[];
  assignments: Assignments;
  nextId: number;
}

const STARTING: Saved = {
  people: [
    { id: "p1", name: "Person 1" },
    { id: "p2", name: "Person 2" },
  ],
  assignments: {},
  nextId: 3,
};

/** The split lives in this browser. It can come back empty, so it's only a convenience. */
function load(key: string): Saved {
  try {
    const saved = JSON.parse(localStorage.getItem(key) ?? "null") as Saved | null;
    if (saved && Array.isArray(saved.people) && saved.assignments) return saved;
  } catch {
    // Blocked or unreadable storage just means starting fresh.
  }
  return STARTING;
}

function store(key: string, saved: Saved) {
  try {
    localStorage.setItem(key, JSON.stringify(saved));
  } catch {
    // Blocked storage means the split lasts until the page closes.
  }
}

function PersonRow({
  person,
  onRename,
  onRemove,
}: {
  person: Person;
  onRename: (name: string) => void;
  onRemove: (() => void) | null;
}) {
  const id = useId();
  return (
    <li className="flex items-center gap-2">
      <label htmlFor={id} className="sr-only">
        Name of {person.name || "this person"}
      </label>
      <input
        id={id}
        value={person.name}
        maxLength={40}
        autoComplete="off"
        onChange={(event) => onRename(event.target.value)}
        className="min-h-10 w-full min-w-0 rounded-control border border-border bg-raised px-3 text-base transition-colors duration-150 hover:border-text-2 sm:text-sm"
      />
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="min-h-10 shrink-0 rounded-control px-2 text-[13px] font-medium text-text-2 underline hover:text-text"
        >
          Remove<span className="sr-only"> {person.name}</span>
        </button>
      )}
    </li>
  );
}

function Toggle({
  pressed,
  label,
  onClick,
}: {
  pressed: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`inline-flex min-h-10 max-w-full items-center gap-1.5 rounded-control border px-3 text-left font-medium transition-colors duration-150 [overflow-wrap:anywhere] ${
        pressed
          ? "border-accent bg-accent-subtle text-accent-strong"
          : "border-border bg-raised text-text-2 hover:border-text-2 hover:text-text"
      }`}
    >
      {pressed && <CheckIcon />}
      {label}
    </button>
  );
}

export function SplitBill({ receipt, storageKey }: { receipt: Receipt; storageKey: string }) {
  const key = `split:${storageKey}`;
  const [saved, setSaved] = useState(() => load(key));
  const { people, assignments, nextId } = saved;
  const result = splitBill(receipt, people, assignments);
  const allIds = people.map((person) => person.id);
  const headingId = useId();

  useEffect(() => store(key, saved), [key, saved]);

  function toggle(index: number, id: string) {
    const current = assignments[index] ?? [];
    const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
    setSaved({ ...saved, assignments: { ...assignments, [index]: next } });
  }

  function everyone(index: number) {
    const current = assignments[index] ?? [];
    const allOn = allIds.every((id) => current.includes(id));
    setSaved({ ...saved, assignments: { ...assignments, [index]: allOn ? [] : allIds } });
  }

  const hasUnassigned = result.unassigned.total !== zero(result.unassigned.total);

  return (
    <section aria-labelledby={headingId} className="min-w-0">
      <h2 id={headingId} className="text-base font-semibold">
        Split the bill
      </h2>

      <h3 className="mt-3 mb-2 text-[13px] font-medium text-text-2">Who's paying?</h3>
      <ul className="grid gap-2 sm:grid-cols-2">
        {people.map((person) => (
          <PersonRow
            key={person.id}
            person={person}
            onRename={(name) =>
              setSaved({
                ...saved,
                people: people.map((p) => (p.id === person.id ? { ...p, name } : p)),
              })
            }
            onRemove={
              people.length > 1
                ? () => setSaved({ ...saved, people: people.filter((p) => p.id !== person.id) })
                : null
            }
          />
        ))}
      </ul>
      <div className="mt-2">
        <Button
          disabled={people.length >= 20}
          onClick={() =>
            setSaved({
              ...saved,
              people: [...people, { id: `p${nextId}`, name: `Person ${nextId}` }],
              nextId: nextId + 1,
            })
          }
        >
          Add a person
        </Button>
      </div>

      {receipt.items.length > 0 && (
        <>
          <h3 className="mt-5 mb-2 text-[13px] font-medium text-text-2">Who had what?</h3>
          <ul className="divide-y divide-border border-y border-border">
            {receipt.items.map((item, index) => {
              const sharers = assignments[index] ?? [];
              return (
                <li key={`${item.name}-${index}`} className="py-3">
                  <div className="flex justify-between gap-4">
                    <span className="min-w-0 font-medium [overflow-wrap:anywhere]">{item.name}</span>
                    <span className="numeric">{grouped(item.total_price)}</span>
                  </div>
                  <div
                    role="group"
                    aria-label={`Who had ${item.name}`}
                    className="mt-2 flex flex-wrap gap-1.5"
                  >
                    {people.map((person) => (
                      <Toggle
                        key={person.id}
                        pressed={sharers.includes(person.id)}
                        label={person.name || "Unnamed"}
                        onClick={() => toggle(index, person.id)}
                      />
                    ))}
                    {people.length > 1 && (
                      <Toggle
                        pressed={allIds.every((id) => sharers.includes(id))}
                        label="Everyone"
                        onClick={() => everyone(index)}
                      />
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <h3 className="mt-5 mb-2 text-[13px] font-medium text-text-2">
        Each person pays{receipt.currency ? ` (${receipt.currency})` : ""}
      </h3>
      <div
        tabIndex={0}
        role="region"
        aria-label="Each person's share"
        className="overflow-x-auto rounded-control border border-border"
      >
        <table className="w-full border-collapse">
          <thead className="bg-surface text-[13px] text-text-2">
            <tr>
              <th scope="col" className="px-3 py-2 text-left font-medium">
                Person
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Items
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Tax and extras
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Pays
              </th>
            </tr>
          </thead>
          <tbody>
            {people.map((person) => {
              const share = result.shares[person.id];
              return (
                <tr key={person.id} className="border-t border-border">
                  <th
                    scope="row"
                    className="min-w-24 px-3 py-2 text-left font-medium [overflow-wrap:anywhere]"
                  >
                    {person.name || "Unnamed"}
                  </th>
                  <td className="numeric px-3 py-2 text-right">{grouped(share?.items ?? "0")}</td>
                  <td className="numeric px-3 py-2 text-right">{grouped(share?.extras ?? "0")}</td>
                  <td className="numeric px-3 py-2 text-right font-medium">
                    {grouped(share?.total ?? "0")}
                  </td>
                </tr>
              );
            })}
            {hasUnassigned && (
              <tr className="border-t border-border bg-surface">
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="text-warn">
                      <AlertIcon />
                    </span>
                    Not assigned yet
                  </span>
                </th>
                <td className="numeric px-3 py-2 text-right">{grouped(result.unassigned.items)}</td>
                <td className="numeric px-3 py-2 text-right">{grouped(result.unassigned.extras)}</td>
                <td className="numeric px-3 py-2 text-right font-medium">
                  {grouped(result.unassigned.total)}
                </td>
              </tr>
            )}
          </tbody>
          <tfoot>
            <tr className="border-t border-border font-semibold">
              <th scope="row" colSpan={3} className="px-3 py-2 text-left">
                Receipt total
              </th>
              <td className="numeric px-3 py-2 text-right">{grouped(receipt.total)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="mt-2 text-[13px] text-text-2">
        Tax, service, tip and discounts are shared in proportion to what each person had. Saved in
        this browser only.
      </p>
    </section>
  );
}

/** "0", or "0.00" at the same precision, so it can be compared as text. */
function zero(value: string): string {
  const places = value.split(".")[1]?.length ?? 0;
  return places > 0 ? `0.${"0".repeat(places)}` : "0";
}
