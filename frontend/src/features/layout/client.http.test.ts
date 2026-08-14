import layoutFixture from "../../../../contracts/fixtures/v0.3/page-layout-draft.json";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PageLayoutDraft } from "../../generated/api/v03Types";
import { createLayoutHttpClient } from "./client";

const fixture = structuredClone(layoutFixture) as PageLayoutDraft;

afterEach(() => vi.unstubAllGlobals());

describe("layout HTTP client", () => {
  it("uses feature-local routes and protected command headers without external requests", async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ path: String(input), init });
        return Promise.resolve(
          new Response(JSON.stringify({ external_requests_started: 0 }), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );
    const client = createLayoutHttpClient({ session: "session-test", csrf: "csrf-test" });
    await client.createDraft("project-1", "chapter-1", "storyboard-1", fixture, "layout-create-1");

    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe("/api/v1/projects/project-1/layouts/drafts");
    const headers = new Headers(calls[0].init?.headers);
    expect(headers.get("X-Manga-Maker-Session")).toBe("session-test");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
    expect(headers.get("Idempotency-Key")).toBe("layout-create-1");
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      chapter_id: "chapter-1",
      storyboard_version_id: "storyboard-1",
    });
  });
});
