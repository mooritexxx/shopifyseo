import { memo } from "react";

const VIEW_W = 160;
const VIEW_H = 36;

type Props = {
  values: number[];
  color: string;
  /** Short description for screen readers, e.g. "Daily clicks this period" */
  ariaLabel: string;
};

/**
 * Tiny trend line from a numeric series (no axes). Skips render if fewer than 2 points.
 */
export const MiniSparkline = memo(function MiniSparkline({ values, color, ariaLabel }: Props) {
  if (values.length < 2) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;

  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * VIEW_W;
      const t = range > 0 ? (v - min) / range : 0.5;
      const y = VIEW_H - 2 - t * (VIEW_H - 4);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg
      width="100%"
      height={VIEW_H}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="none"
      className="block max-w-full"
      role="img"
      aria-label={ariaLabel}
    >
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  );
});
