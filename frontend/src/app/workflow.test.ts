import { describe, expect, it } from "vitest";

import { V03_FEATURE_ORDER } from "./index";

describe("v0.3 app composition", () => {
  it("composes only feature public entries in the planned workflow order", () => {
    expect(V03_FEATURE_ORDER).toEqual([
      "adaptation",
      "layout",
      "prompting",
      "production",
      "review",
      "composition",
      "asset_catalog",
      "exporting",
    ]);
  });
});
