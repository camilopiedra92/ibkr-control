import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { IngestHealthBanner } from "./IngestHealthBanner";


function makeHealth(overrides: Partial<{
  flexLastSuccessAt: string | null;
  flexConsecutiveFailures: number;
  trmLastSuccessAt: string | null;
  trmConsecutiveFailures: number;
}> = {}) {
  const now = new Date().toISOString();
  return {
    sources: [
      {
        source: "flex" as const,
        last_success_at: overrides.flexLastSuccessAt ?? now,
        last_failure_at: null,
        consecutive_failures: overrides.flexConsecutiveFailures ?? 0,
        last_error: null,
      },
      {
        source: "trm" as const,
        last_success_at: overrides.trmLastSuccessAt ?? now,
        last_failure_at: null,
        consecutive_failures: overrides.trmConsecutiveFailures ?? 0,
        last_error: null,
      },
    ],
    checked_at: now,
  };
}


describe("IngestHealthBanner", () => {
  it("renders nothing when all sources healthy", () => {
    render(<IngestHealthBanner health={makeHealth()} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders yellow warning when last success >24h ago and <=48h", () => {
    const stale = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString();
    render(<IngestHealthBanner health={makeHealth({ flexLastSuccessAt: stale })} />);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveAttribute("data-severity", "warning");
    expect(banner).toHaveTextContent(/Ingesta sin actualizar/i);
  });

  it("renders red alert when consecutive_failures >= 2", () => {
    render(<IngestHealthBanner health={makeHealth({ flexConsecutiveFailures: 3 })} />);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveAttribute("data-severity", "error");
    expect(banner).toHaveTextContent(/Falla detectada en ingesta/i);
  });

  it("renders red alert when last success >48h ago", () => {
    const tooOld = new Date(Date.now() - 49 * 60 * 60 * 1000).toISOString();
    render(<IngestHealthBanner health={makeHealth({ flexLastSuccessAt: tooOld })} />);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveAttribute("data-severity", "error");
  });
});
