import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Home from "./page";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../lib/api", () => ({ api: request }));

describe("browser inspection output", () => {
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
      return Promise.reject(new Error("unexpected request"));
    });
  });

  it("renders stored browser payloads as inert React text", async () => {
    render(<Home />);
    await screen.findByText("Browser reinspection");
    fireEvent.change(screen.getByLabelText("Website ID"), {
      target: { value: "12345678-1234-1234-1234-123456789abc" },
    });
    fireEvent.submit(
      screen.getByText("Request browser inspection").closest("form")!,
    );

    await screen.findByText("<script>globalThis.titleAttack=true</script>");
    await screen.findByText(
      "<img src=x onerror='globalThis.textAttack=true'><svg onload='globalThis.svgAttack=true'>",
    );
    await waitFor(() => expect(request).toHaveBeenCalledTimes(4));
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("svg")).toBeNull();
    expect(
      (globalThis as typeof globalThis & { titleAttack?: boolean }).titleAttack,
    ).toBeUndefined();
  });
});
