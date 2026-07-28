import { describe, expect, it } from "vitest";
import { csrfToken } from "./api";

describe("csrfToken", () => {
  it("reads the non-HttpOnly CSRF cookie", () => {
    document.cookie = "csrf_token=test-value";
    expect(csrfToken()).toBe("test-value");
  });
});
