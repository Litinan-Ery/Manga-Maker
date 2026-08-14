import layoutFixture from "../../../../contracts/fixtures/v0.3/page-layout-draft.json";
import sixPanelFixture from "../../../../contracts/fixtures/v0.3/page-layout-six-panel.json";
import { describe, expect, it } from "vitest";

import type { PageLayoutDraft } from "../../generated/api/v03Types";
import type { StoryboardPageSummary } from "./client";
import {
  LAYOUT_TEMPLATES,
  absoluteFrameRect,
  changePageProfile,
  createLayoutDraft,
  frameDepth,
  leafFrames,
  mergeFrame,
  moveReadingOrder,
  splitFrame,
  updateFrameAbsoluteRect,
} from "./templates";

const twoPanel = structuredClone(layoutFixture) as PageLayoutDraft;
const sixPanel = structuredClone(sixPanelFixture) as PageLayoutDraft;

describe("layout templates and hierarchy transforms", () => {
  it("provides exactly one deterministic starter for every 1–6 panel count", () => {
    expect(LAYOUT_TEMPLATES.map((template) => template.rects.length)).toEqual([1, 2, 3, 4, 5, 6]);

    for (const template of LAYOUT_TEMPLATES) {
      const page = pageWithPanels(template.rects.length);
      const draft = createLayoutDraft(page, template);
      expect(leafFrames(draft)).toHaveLength(template.rects.length);
      expect(leafFrames(draft).map((frame) => frame.panel_id)).toEqual(
        page.panels.map((panel) => panel.panel_id),
      );
      expect(draft.frames.filter((frame) => frame.parent_frame_id === null)).toHaveLength(1);
    }
  });

  it("splits and merges hierarchy without changing panel coverage or absolute geometry", () => {
    const root = sixPanel.frames.find((frame) => frame.parent_frame_id === null)!;
    const beforePanels = leafFrames(sixPanel).map((frame) => frame.panel_id);
    const beforeRects = new Map(
      leafFrames(sixPanel).map((frame) => [frame.frame_id, absoluteFrameRect(sixPanel, frame.frame_id)]),
    );

    const split = splitFrame(sixPanel, root.frame_id, "vertical");
    expect(split.frames).toHaveLength(sixPanel.frames.length + 2);
    expect(leafFrames(split).map((frame) => frame.panel_id)).toEqual(beforePanels);
    expect(Math.max(...leafFrames(split).map((frame) => frameDepth(split, frame.frame_id)))).toBe(2);
    for (const leaf of leafFrames(split)) {
      expect(absoluteFrameRect(split, leaf.frame_id)).toEqual(beforeRects.get(leaf.frame_id));
    }

    const merged = mergeFrame(split, root.frame_id);
    expect(merged.frames).toHaveLength(sixPanel.frames.length);
    expect(leafFrames(merged).map((frame) => frame.panel_id)).toEqual(beforePanels);
    for (const leaf of leafFrames(merged)) {
      expect(absoluteFrameRect(merged, leaf.frame_id)).toEqual(beforeRects.get(leaf.frame_id));
    }
  });

  it("keeps absolute drag geometry valid inside a nested parent", () => {
    const root = sixPanel.frames.find((frame) => frame.parent_frame_id === null)!;
    const split = splitFrame(sixPanel, root.frame_id, "horizontal");
    const leaf = leafFrames(split)[0];
    const before = absoluteFrameRect(split, leaf.frame_id);
    const moved = updateFrameAbsoluteRect(split, leaf.frame_id, {
      ...before,
      x: before.x + 0.01,
    });
    expect(absoluteFrameRect(moved, leaf.frame_id).x).toBeCloseTo(before.x + 0.01, 10);
    expect(moved.frames.find((frame) => frame.frame_id === leaf.frame_id)?.aspect_ratio).toBeCloseTo(
      leaf.aspect_ratio,
      10,
    );
  });

  it("recomputes aspect ratios for vertical-strip profile and swaps reading order contiguously", () => {
    const vertical = changePageProfile(twoPanel, "vertical_strip");
    expect(vertical.canvas).toEqual({ width: 1536, height: 4096 });
    expect(vertical.frames[0].aspect_ratio).toBeCloseTo(0.375, 10);

    const leaves = leafFrames(vertical);
    const reordered = moveReadingOrder(vertical, leaves[0].frame_id, 1);
    expect(reordered.frames.find((frame) => frame.frame_id === leaves[0].frame_id)?.order).toBe(2);
    expect(reordered.frames.find((frame) => frame.frame_id === leaves[1].frame_id)?.order).toBe(1);
  });
});

function pageWithPanels(count: number): StoryboardPageSummary {
  return {
    page_id: "01900000-0000-7000-8000-000000009000",
    page_number: 1,
    turning_point: "template fixture",
    panels: Array.from({ length: count }, (_, index) => ({
      panel_id: `01900000-0000-7000-8000-${String(index + 1).padStart(12, "0")}`,
      order: index + 1,
      purpose: `panel ${index + 1}`,
      characters: [],
    })),
  };
}
