import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api, ApiError, type Issue, type RoutedDocument } from "../api";
import {
  AMOUNT_FIELDS,
  type AmountField,
  blankItem,
  draftProblem,
  fromDraft,
  type ItemDraft,
  issuesAt,
  toDraft,
} from "../receiptDraft";
import { FlagIcon } from "./Icons";
import { Button } from "./States";

const AMOUNT_LABELS: Record<AmountField, string> = {
  subtotal: "Subtotal",
  discount: "Discount",
  service_charge: "Service charge",
  tax: "Tax",
  tip: "Tip",
};

const INPUT =
  "min-h-10 w-full rounded-control border border-border bg-raised px-3 text-base transition-colors duration-150 hover:border-text-2 aria-invalid:border-warn sm:text-sm";

function Field({
  label,
  value,
  onChange,
  flagged = false,
  numeric = true,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  flagged?: boolean;
  numeric?: boolean;
}) {
  const id = useId();
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="block text-[13px] font-medium text-text-2">
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={flagged}
        inputMode={numeric ? "decimal" : undefined}
        autoComplete="off"
        className={`${INPUT} mt-1 ${numeric ? "numeric" : ""}`}
      />
    </div>
  );
}

/** Says what's wrong in words, next to the part it's about. */
function IssueNotes({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return null;
  return (
    <ul className="mt-2 grid gap-1">
      {issues.map((issue, index) => (
        <li key={`${issue.code}-${index}`} className="flex gap-2 [overflow-wrap:anywhere]">
          <span className="mt-0.5 text-warn">
            <FlagIcon />
          </span>
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

function ItemRow({
  item,
  number,
  issues,
  onChange,
  onRemove,
}: {
  item: ItemDraft;
  number: number;
  issues: Issue[];
  onChange: (item: ItemDraft) => void;
  onRemove: (() => void) | null;
}) {
  const flagged = issues.length > 0;
  return (
    <li
      className={`rounded-control border p-3 ${flagged ? "border-warn bg-surface" : "border-border"}`}
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-[minmax(0,3fr)_minmax(0,1fr)_minmax(0,2fr)_minmax(0,2fr)_minmax(0,2fr)]">
        <div className="col-span-2 sm:col-span-1">
          <Field
            label={`Line ${number} name`}
            value={item.name}
            numeric={false}
            onChange={(name) => onChange({ ...item, name })}
          />
        </div>
        <Field
          label="Qty"
          value={item.quantity}
          onChange={(quantity) => onChange({ ...item, quantity })}
        />
        <Field
          label="Unit price"
          value={item.unit_price}
          flagged={flagged}
          onChange={(unit_price) => onChange({ ...item, unit_price })}
        />
        <Field
          label="Line total"
          value={item.total_price}
          flagged={flagged}
          onChange={(total_price) => onChange({ ...item, total_price })}
        />
        <Field
          label="Discount"
          value={item.discount}
          onChange={(discount) => onChange({ ...item, discount })}
        />
      </div>
      <IssueNotes issues={issues} />
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="mt-2 min-h-8 rounded-badge text-[13px] font-medium text-text-2 underline hover:text-text"
        >
          Remove line {number}
        </button>
      )}
    </li>
  );
}

function issuesFrom(error: unknown): string[] {
  const details = error instanceof ApiError ? (error.details as { issues?: string[] } | null) : null;
  return details?.issues ?? [];
}

export function ReceiptEditor({
  document,
  onSaved,
}: {
  document: RoutedDocument;
  onSaved: (saved: RoutedDocument) => void;
}) {
  const [draft, setDraft] = useState(() => toDraft(document.receipt));
  const [problem, setProblem] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const currency = document.receipt?.currency ?? null;

  const save = useMutation({
    mutationFn: (confirmMismatch: boolean) =>
      api.correct(document.digest, fromDraft(draft, currency), confirmMismatch),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      onSaved(saved);
    },
  });
  const rejected = save.error instanceof ApiError && save.error.code === "correction_rejected";

  function submit() {
    const found = draftProblem(draft);
    setProblem(found);
    if (!found) save.mutate(false);
  }

  function setItem(index: number, item: ItemDraft) {
    setDraft({ ...draft, items: draft.items.map((old, i) => (i === index ? item : old)) });
    save.reset();
  }

  return (
    <form
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="grid gap-4"
    >
      <Field
        label="Shop"
        value={draft.vendor}
        numeric={false}
        onChange={(vendor) => setDraft({ ...draft, vendor })}
      />

      <fieldset className="min-w-0">
        <legend className="mb-2 text-base font-semibold">Line items</legend>
        <ul className="grid gap-2">
          {draft.items.map((item, index) => (
            <ItemRow
              key={item.id}
              item={item}
              number={index + 1}
              issues={issuesAt(document.issues, `items[${index}]`)}
              onChange={(changed) => setItem(index, changed)}
              onRemove={
                draft.items.length > 1
                  ? () =>
                      setDraft({ ...draft, items: draft.items.filter((_, i) => i !== index) })
                  : null
              }
            />
          ))}
        </ul>
        <div className="mt-2">
          <Button onClick={() => setDraft({ ...draft, items: [...draft.items, blankItem()] })}>
            Add a line
          </Button>
        </div>
      </fieldset>

      <fieldset className="min-w-0">
        <legend className="mb-2 text-base font-semibold">Amounts</legend>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {AMOUNT_FIELDS.map((field) => (
            <Field
              key={field}
              label={AMOUNT_LABELS[field]}
              value={draft.amounts[field]}
              flagged={issuesAt(document.issues, field).length > 0}
              onChange={(value) => {
                setDraft({ ...draft, amounts: { ...draft.amounts, [field]: value } });
                save.reset();
              }}
            />
          ))}
          <Field
            label="Total"
            value={draft.total}
            flagged={issuesAt(document.issues, "total").length > 0}
            onChange={(total) => {
              setDraft({ ...draft, total });
              save.reset();
            }}
          />
        </div>
        <IssueNotes
          issues={document.issues.filter((issue) => !issue.location.startsWith("items["))}
        />
      </fieldset>

      {problem && (
        <p role="alert" className="text-down">
          {problem}
        </p>
      )}
      {save.isError && (
        <div role="alert" className="rounded-control border border-border bg-surface px-4 py-3">
          <p className="font-medium">{save.error.message}</p>
          {issuesFrom(save.error).length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-text-2">
              {issuesFrom(save.error).map((issue) => (
                <li key={issue} className="[overflow-wrap:anywhere]">
                  {issue}
                </li>
              ))}
            </ul>
          )}
          {rejected && (
            <div className="mt-3">
              <Button disabled={save.isPending} onClick={() => save.mutate(true)}>
                Save anyway, the receipt is misprinted
              </Button>
            </div>
          )}
        </div>
      )}
      <div>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save correction"}
        </Button>
      </div>
    </form>
  );
}
