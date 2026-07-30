import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Home from "./page";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../lib/api", () => ({ api: request }));

describe("browser inspection output", () => {
  afterEach(cleanup);
  beforeEach(() => {
    request.mockReset();
    request.mockImplementation((path: string) => {
      if (path === "/api/auth/me") {
        return Promise.resolve({
          id: "admin-id",
          email: "admin@example.com",
          role: "admin",
        });
      }
      if (path === "/api/categories" || path === "/api/age-policies") {
        return Promise.resolve([]);
      }
      if (path === "/api/ai/settings") {
        return Promise.resolve({
          enabled: false,
          provider: "disabled",
          model: "",
          confidence_threshold: 0.75,
          conflict_threshold: 0.1,
          screenshot_enabled: false,
          retry_limit: 2,
          daily_request_limit: 100,
          monthly_cost_limit: 0,
        });
      }
      if (path.includes("/api/browser/websites/")) {
        return Promise.resolve({
          browser: {
            id: "browser-id",
            run_id: "run-id",
            status: "completed",
            rendered_title: "<script>globalThis.titleAttack=true</script>",
            rendered_text_sample:
              "<img src=x onerror='globalThis.textAttack=true'><svg onload='globalThis.svgAttack=true'>",
          },
        });
      }
      if (path.includes("/api/ai/websites/")) {
        return Promise.resolve({
          ai: {
            id: "ai-id",
            run_id: "run-id",
            status: "completed",
            provider: "<img src=x onerror='globalThis.providerAttack=true'>",
            model: "<script>globalThis.modelAttack=true</script>",
            confidence: 91,
            evidence: ["<svg onload='globalThis.evidenceAttack=true'>"],
            prompt_injection_suspected: true,
            validation_status: "valid",
            manual_review_required: false,
            promoted: true,
          },
        });
      }
      return Promise.reject(new Error("unexpected request"));
    });
  });

  it("renders stored browser payloads as inert React text", async () => {
    render(<Home />);
    await screen.findByText("Browser reinspection");
    const form = screen
      .getByText("Request browser inspection")
      .closest("form")!;
    fireEvent.change(form.querySelector("input[name=website_id]")!, {
      target: { value: "12345678-1234-1234-1234-123456789abc" },
    });
    fireEvent.submit(form);

    await screen.findByText("<script>globalThis.titleAttack=true</script>");
    await screen.findByText(
      "<img src=x onerror='globalThis.textAttack=true'><svg onload='globalThis.svgAttack=true'>",
    );
    await waitFor(() => expect(request).toHaveBeenCalledTimes(5));
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("svg")).toBeNull();
    expect(
      (globalThis as typeof globalThis & { titleAttack?: boolean }).titleAttack,
    ).toBeUndefined();
  });

  it("renders AI provider evidence as inert text", async () => {
    render(<Home />);
    await screen.findByText("AI classification fallback");
    const form = screen.getByText("Request AI recommendation").closest("form")!;
    fireEvent.change(form.querySelector("input")!, {
      target: { value: "12345678-1234-1234-1234-123456789abc" },
    });
    fireEvent.submit(form);
    await screen.findByText("<svg onload='globalThis.evidenceAttack=true'>");
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("svg")).toBeNull();
  });
});
