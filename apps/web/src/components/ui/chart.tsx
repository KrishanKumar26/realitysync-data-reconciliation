import { cn } from "@/lib/utils";

/**
 * Charts.
 *
 * Hand-drawn SVG rather than a charting library. The shapes this product needs
 * are compositions and ratios of small integers; a library would add a
 * significant bundle to draw three arcs and some rectangles.
 *
 * What is deliberately absent: line charts, sparklines and anything else that
 * implies a trend. The API exposes counts and compositions, not time series —
 * `observation_count`, `by_severity`, `syncs_in_window`. Drawing a trend line
 * would mean bucketing the truncated activity feed and presenting the result as
 * history, which is precisely the sort of confident-looking-but-unfounded
 * graphic this product exists to argue against. When the API gains a real
 * time series, a real chart can be added here.
 *
 * Every chart is decoration around a number that is also stated in text. None
 * of them is the only place a value appears.
 */

export interface Slice {
  label: string;
  value: number;
  /** A CSS colour, normally a var(--color-*) token. */
  color: string;
}

/* ------------------------------------------------------------------------ */

/**
 * Donut.
 *
 * Composition of a whole — source status, for example. The total sits in the
 * middle because "how many altogether" is asked as often as "how are they
 * split", and a donut with an empty hole wastes the one place the eye lands.
 */
export function Donut({
  slices,
  total,
  caption,
  size = 132,
  thickness = 14,
  className,
}: {
  slices: Slice[];
  /** Defaults to the sum of the slices. */
  total?: number;
  caption?: string;
  size?: number;
  thickness?: number;
  className?: string;
}) {
  const sum = slices.reduce((acc, slice) => acc + slice.value, 0);
  const whole = total ?? sum;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;

  let offset = 0;

  return (
    <div className={cn("flex flex-wrap items-center gap-5", className)}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="shrink-0 -rotate-90"
        role="img"
        aria-label={
          caption ??
          slices.map((slice) => `${slice.label}: ${slice.value}`).join(", ")
        }
      >
        {/* Track. Also the whole chart when there is nothing to show, which is
            better than an empty box that reads as a rendering failure. */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={thickness}
        />
        {sum > 0
          ? slices
              .filter((slice) => slice.value > 0)
              .map((slice) => {
                const fraction = slice.value / sum;
                const dash = fraction * circumference;
                const element = (
                  <circle
                    key={slice.label}
                    cx={size / 2}
                    cy={size / 2}
                    r={radius}
                    fill="none"
                    stroke={slice.color}
                    strokeWidth={thickness}
                    strokeDasharray={`${dash} ${circumference - dash}`}
                    strokeDashoffset={-offset}
                    strokeLinecap="butt"
                  />
                );
                offset += dash;
                return element;
              })
          : null}
      </svg>

      <div className="min-w-0">
        <p className="tabular text-3xl font-semibold tracking-tight text-foreground">
          {whole.toLocaleString()}
        </p>
        {caption ? (
          <p className="mt-0.5 text-xs text-muted-foreground">{caption}</p>
        ) : null}
        <ul className="mt-3 space-y-1.5">
          {slices.map((slice) => (
            <li key={slice.label} className="flex items-center gap-2 text-xs">
              <span
                aria-hidden="true"
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: slice.color }}
              />
              <span className="text-muted-foreground">{slice.label}</span>
              <span className="tabular ml-auto pl-3 text-foreground">
                {slice.value.toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */

/**
 * Labelled horizontal bars.
 *
 * Better than a donut when categories are ordered (severity) or when one
 * category dwarfs the others, both of which make arc lengths hard to compare.
 */
export function BarRows({
  rows,
  className,
}: {
  rows: Slice[];
  className?: string;
}) {
  const max = Math.max(...rows.map((row) => row.value), 1);

  return (
    <ul className={cn("space-y-2.5", className)}>
      {rows.map((row) => (
        <li key={row.label}>
          <div className="flex items-baseline justify-between gap-3 text-xs">
            <span className="truncate text-muted-foreground">{row.label}</span>
            <span className="tabular shrink-0 font-medium text-foreground">
              {row.value.toLocaleString()}
            </span>
          </div>
          <div
            className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted"
            role="img"
            aria-label={`${row.label}: ${row.value}`}
          >
            <div
              className="h-full rounded-full transition-[width] duration-500 ease-out"
              style={{
                width: `${Math.max((row.value / max) * 100, row.value > 0 ? 4 : 0)}%`,
                backgroundColor: row.color,
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------------ */

/**
 * A single part-of-whole bar.
 *
 * For "38 of 40 syncs succeeded" — a ratio where the remainder is as meaningful
 * as the part, so both ends are labelled.
 */
export function RatioBar({
  value,
  total,
  label,
  valueLabel,
  tone = "accent",
  className,
}: {
  value: number;
  total: number;
  label: string;
  valueLabel?: string;
  tone?: "accent" | "healthy" | "degraded" | "down";
  className?: string;
}) {
  const percent = total > 0 ? (value / total) * 100 : 0;
  const fill = {
    accent: "var(--color-accent-cyan)",
    healthy: "var(--color-status-healthy)",
    degraded: "var(--color-status-degraded)",
    down: "var(--color-status-down)",
  }[tone];

  return (
    <div className={className}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="tabular text-xs font-medium text-foreground">
          {valueLabel ??
            `${value.toLocaleString()} / ${total.toLocaleString()}`}
        </span>
      </div>
      <div
        className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={`${label}: ${value} of ${total}`}
      >
        <div
          className="h-full rounded-full transition-[width] duration-500 ease-out"
          style={{
            width: `${Math.min(percent, 100)}%`,
            backgroundColor: fill,
          }}
        />
      </div>
    </div>
  );
}
