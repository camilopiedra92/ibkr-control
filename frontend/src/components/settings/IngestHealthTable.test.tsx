import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { IngestHealthTable } from "./IngestHealthTable";


const baseHealth = {
  sources: [
    {
      source: "flex" as const,
      last_success_at: new Date().toISOString(),
      last_failure_at: null,
      consecutive_failures: 0,
      last_error: null,
    },
    {
      source: "trm" as const,
      last_success_at: new Date().toISOString(),
      last_failure_at: null,
      consecutive_failures: 0,
      last_error: null,
    },
  ],
  checked_at: new Date().toISOString(),
};


describe("IngestHealthTable", () => {
  it("renders one row per source", () => {
    render(<IngestHealthTable health={baseHealth} recentLogs={[]} />);
    expect(screen.getByText("flex")).toBeInTheDocument();
    expect(screen.getByText("trm")).toBeInTheDocument();
  });

  it("displays last_error truncated with title tooltip", () => {
    const health = {
      ...baseHealth,
      sources: [
        {
          source: "flex" as const,
          last_success_at: null,
          last_failure_at: new Date().toISOString(),
          consecutive_failures: 1,
          last_error: "timeout fetching SendRequest",
        },
        baseHealth.sources[1],
      ],
    };
    render(<IngestHealthTable health={health} recentLogs={[]} />);
    const cell = screen.getByText("timeout fetching SendRequest");
    expect(cell).toBeInTheDocument();
    // Tooltip via title attr
    expect(cell).toHaveAttribute("title", "timeout fetching SendRequest");
  });

  it("renders sparkline bars when recentLogs provided", () => {
    const now = Date.now();
    const recentLogs = [
      { id: 1, job_kind: "flex", status: "ok", started_at: new Date(now - 1000).toISOString(), finished_at: new Date(now).toISOString(), trigger: "cron", items_processed: 100, error_message: null, user_id: 1 },
      { id: 2, job_kind: "flex", status: "failed", started_at: new Date(now - 2000).toISOString(), finished_at: new Date(now - 1000).toISOString(), trigger: "cron", items_processed: 0, error_message: "boom", user_id: 1 },
    ];
    const { container } = render(
      <IngestHealthTable health={baseHealth} recentLogs={recentLogs as never} />
    );
    // 2 flex bars + 0 trm bars = 2 SVG rects total in flex row
    const rects = container.querySelectorAll("rect");
    expect(rects.length).toBeGreaterThanOrEqual(2);
  });
});
