import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DeltaInline, SegmentMixTile } from "./overview-cards";
import { Globe } from "lucide-react";
import { periodDays, PreviousPeriodComparison } from "./overview-reporting";

describe("Overview reporting semantics", () => {
  it("shows a falling bounce rate as improvement while preserving the downward arrow", () => {
    render(<DeltaInline pct={-12.4} unit="points" lowerIsBetter />);
    expect(screen.getByText("↓ 12.4 pp vs prior")).toHaveClass("text-emerald-600");
  });
  it("shows a rising bounce rate as worsening", () => {
    render(<DeltaInline pct={4} unit="points" lowerIsBetter />);
    expect(screen.getByText("↑ 4.0 pp vs prior")).toHaveClass("text-rose-600");
  });
  it("calculates inclusive calendar days across daylight-saving and leap dates", () => {
    expect(periodDays({start_date: "2026-03-01", end_date: "2026-03-31"})).toBe(31);
    expect(periodDays({start_date: "2024-02-01", end_date: "2024-02-29"})).toBe(29);
    expect(periodDays({start_date: "bad", end_date: "2024-02-29"})).toBe(0);
  });
  it("does not offer a comparison when no prior window exists", () => {
    render(<PreviousPeriodComparison previous={null} checked={false} onChange={() => {}} />);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
  it("labels audience shares as returned rows and identifies the highest-impression country", () => {
    render(<SegmentMixTile label="Country" dimension="country" icon={Globe} slice={{rows:[
      {keys:["usa"], impressions:20}, {keys:["can"], impressions:80}
    ]}} />);
    expect(screen.getByText("Canada")).toBeInTheDocument();
    expect(screen.getByText("80.0% of returned impressions")).toBeInTheDocument();
  });
});
