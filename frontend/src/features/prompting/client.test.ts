import { afterEach, describe, expect, it, vi } from "vitest";

import { createPromptInspectorHttpClient } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("prompt inspector HTTP client", () => {
  it("loads the exact snapshot without session or credential headers", async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ path: String(input), init });
        return Promise.resolve(
          new Response(JSON.stringify({ external_requests_started: 0 }), {
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );

    await createPromptInspectorHttpClient().inspect("project-1", "prompt-1", "a".repeat(64));

    expect(calls).toHaveLength(1);
    expect(calls[0].path).toContain("snapshot_sha256=" + "a".repeat(64));
    const headers = new Headers(calls[0].init?.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("Authorization")).toBe(false);
    expect(headers.has("X-Manga-Maker-Session")).toBe(false);
  });

  it("surfaces a stale snapshot as a typed conflict", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "PROMPT_INSPECTOR_SNAPSHOT_STALE",
                message: "PromptPlan 预览已经变化，请刷新后重试。",
              },
            }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );

    await expect(
      createPromptInspectorHttpClient().inspect("project-1", "prompt-1", "a".repeat(64)),
    ).rejects.toMatchObject({ status: 409, code: "PROMPT_INSPECTOR_SNAPSHOT_STALE" });
  });
});
