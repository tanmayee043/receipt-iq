import type { Receipt } from "../api";
import { grouped } from "../split";

function Amount({ label, value, strong = false }: { label: string; value: string | null; strong?: boolean }) {
  if (value === null) return null;
  return (
    <div className={`flex justify-between gap-4 py-1 ${strong ? "font-semibold" : ""}`}>
      <dt className={strong ? "" : "text-text-2"}>{label}</dt>
      <dd className="numeric">{grouped(value)}</dd>
    </div>
  );
}

export function ReceiptView({ receipt }: { receipt: Receipt }) {
  return (
    <div className="min-w-0">
      {receipt.vendor && <p className="mb-2 font-medium [overflow-wrap:anywhere]">{receipt.vendor}</p>}
      {receipt.items.length === 0 ? (
        <p className="text-text-2">No line items were found.</p>
      ) : (
        <ul className="divide-y divide-border border-y border-border">
          {receipt.items.map((item, index) => (
            <li key={`${item.name}-${index}`} className="flex justify-between gap-4 py-2">
              <span className="min-w-0 [overflow-wrap:anywhere]">
                {item.name}
                {item.quantity !== "1" && (
                  <span className="numeric ml-1.5 text-text-2">× {item.quantity}</span>
                )}
                {item.discount && (
                  <span className="block text-[13px] text-text-2">
                    Discount <span className="numeric">{grouped(item.discount)}</span>
                  </span>
                )}
              </span>
              <span className="numeric">{grouped(item.total_price)}</span>
            </li>
          ))}
        </ul>
      )}
      <dl className="mt-2">
        <Amount label="Subtotal" value={receipt.subtotal} />
        <Amount label="Discount" value={receipt.discount} />
        <Amount label="Service charge" value={receipt.service_charge} />
        <Amount label="Tax" value={receipt.tax} />
        <Amount label="Tip" value={receipt.tip} />
        <Amount label="Total" value={receipt.total} strong />
      </dl>
    </div>
  );
}
