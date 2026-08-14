import layoutFixture from "../../../../contracts/fixtures/v0.3/page-layout-draft.json";
import { describe, expect, it } from "vitest";

import { ContractApiError } from "../../generated/api/contractError";
import type { PageLayoutDraft } from "../../generated/api/v03Types";
import { createLayoutFixtureClient } from "./client.fixture";

const fixture = layoutFixture as PageLayoutDraft;

describe("layout feature fixture client", () => {
  it("provides detached success fixtures without using the legacy api client", async () => {
    const client = createLayoutFixtureClient(fixture);
    const first = await client.getDraft("project-1", fixture.page_layout_draft_id);
    first.frames[1].focal_point.x = 0.2;

    const second = await client.getDraft("project-1", fixture.page_layout_draft_id);
    expect(second.frames[1].focal_point.x).toBe(0.42);
    await expect(
      client.saveDraft(
        "project-1",
        crypto.randomUUID(),
        "storyboard-version-1",
        second,
        1,
        "save-1",
      ),
    ).resolves.toMatchObject({ revision: 2 });
  });

  it.each([
    ["not_found", 404, "LAYOUT_NOT_FOUND"],
    ["validation", 422, "LAYOUT_INVALID"],
    ["revision_conflict", 409, "LAYOUT_REVISION_CONFLICT"],
  ] as const)("returns the %s contract", async (scenario, status, code) => {
    const client = createLayoutFixtureClient(fixture, {
      getError: scenario === "not_found" ? "not_found" : undefined,
      saveError: scenario === "not_found" ? undefined : scenario,
    });

    const action =
      scenario === "not_found"
        ? client.getDraft("project-1", fixture.page_layout_draft_id)
        : client.saveDraft(
            "project-1",
            crypto.randomUUID(),
            "storyboard-version-1",
            fixture,
            1,
            "save-error",
          );
    await expect(action).rejects.toMatchObject({ status, code } satisfies Partial<ContractApiError>);
  });
});
