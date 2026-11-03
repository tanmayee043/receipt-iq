import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Provider } from "../api";
import { dollars, percent, providerName, seconds, shortName } from "../format";

// Where each name goes, so nearby points don't collide: GPT and Gemma sit close together,
// and Azure is at the right edge.
const LABEL_SPOTS: Record<string, { dx: number; dy: number; anchor: "start" | "middle" | "end" }> =
  {
    gemma: { dx: 0, dy: -13, anchor: "start" },
    azure: { dx: -11, dy: 4, anchor: "end" },
  };
const DEFAULT_SPOT = { dx: 11, dy: 4, anchor: "start" } as const;

interface MarkProps {
  cx?: number;
  cy?: number;
  payload?: Provider;
}

function Mark({ cx = 0, cy = 0, payload }: MarkProps) {
  if (!payload) return null;
  const spot = LABEL_SPOTS[payload.provider] ?? DEFAULT_SPOT;
  return (
    <g>
      {/* Bigger than the mark, so it's easy to hover. */}
      <circle cx={cx} cy={cy} r={14} fill="transparent" />
      <circle
        cx={cx}
        cy={cy}
        r={6}
        fill={payload.meets_policy ? "var(--accent)" : "var(--bg)"}
        stroke={payload.meets_policy ? "var(--bg)" : "var(--text-2)"}
        strokeWidth={2}
      />
      <text
        x={cx + spot.dx}
        y={cy + spot.dy}
        textAnchor={spot.anchor}
        fill="var(--text)"
        fontSize={13}
        fontWeight={500}
      >
        {shortName(payload.provider)}
      </text>
    </g>
  );
}

function PointTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: readonly { payload?: Provider }[];
}) {
  const provider = payload?.[0]?.payload;
  if (!active || !provider) return null;
  return (
    <div className="rounded-control border border-border bg-raised px-3 py-2 text-[13px]">
      <p className="font-medium">{providerName(provider.provider)}</p>
      <p className="text-text-2">
        <span className="numeric text-text">{percent(provider.fully_correct)}</span> fully correct
      </p>
      <p className="text-text-2">
        <span className="numeric text-text">{dollars(provider.cost_per_1000)}</span> per 1,000
      </p>
      <p className="text-text-2">
        <span className="numeric text-text">{seconds(provider.median_seconds)}</span> median
      </p>
    </div>
  );
}

export function AccuracyCostChart({ providers, target }: { providers: Provider[]; target: number }) {
  const description = providers
    .map(
      (provider) =>
        `${providerName(provider.provider)}: ${percent(provider.fully_correct)} at ${dollars(provider.cost_per_1000)}`,
    )
    .join("; ");

  return (
    <figure>
      <figcaption className="mb-3 text-base font-semibold">Accuracy against cost</figcaption>
      <div
        role="img"
        aria-label={`Fully correct rate against cost per 1,000 receipts. ${description}`}
      >
        <ResponsiveContainer width="100%" height={300}>
          <ScatterChart margin={{ top: 16, right: 12, bottom: 28, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" />
            <XAxis
              type="number"
              dataKey="cost_per_1000"
              domain={[0, 10]}
              ticks={[0, 2, 4, 6, 8, 10]}
              padding={{ left: 14, right: 14 }}
              tickFormatter={(value: number) => `$${value}`}
              tick={{ fill: "var(--text-2)", fontSize: 12 }}
              stroke="var(--border)"
              tickLine={false}
              label={{
                value: "Cost per 1,000 receipts",
                position: "bottom",
                offset: 8,
                fill: "var(--text-2)",
                fontSize: 13,
              }}
            />
            <YAxis
              type="number"
              dataKey="fully_correct"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={(value: number) => `${value * 100}%`}
              tick={{ fill: "var(--text-2)", fontSize: 12 }}
              stroke="var(--border)"
              tickLine={false}
              width={44}
            />
            <ReferenceLine
              y={target}
              stroke="var(--accent)"
              strokeWidth={1}
              label={{
                value: `${Math.round(target * 100)}% target`,
                position: "insideBottomRight",
                fill: "var(--text-2)",
                fontSize: 12,
              }}
            />
            <Tooltip content={PointTooltip} cursor={false} isAnimationActive={false} />
            <Scatter data={providers} shape={Mark} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-text-2">
        <li className="flex items-center gap-2">
          <span aria-hidden="true" className="size-3 rounded-full bg-accent" />
          Meets the router's policy
        </li>
        <li className="flex items-center gap-2">
          <span aria-hidden="true" className="size-3 rounded-full border-2 border-text-2 bg-bg" />
          Last resort only
        </li>
      </ul>
    </figure>
  );
}
