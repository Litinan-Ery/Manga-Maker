import type {
  FrameSpec,
  NormalizedRect,
  PageLayoutDraft,
  PageProfile,
  ShotScale,
} from "../../generated/api/v03Types";
import type { StoryboardPageSummary } from "./client";

export interface LayoutTemplate {
  id: string;
  label: string;
  rects: NormalizedRect[];
}

const rect = (x: number, y: number, width: number, height: number): NormalizedRect => ({
  x,
  y,
  width,
  height,
});

export const LAYOUT_TEMPLATES: LayoutTemplate[] = [
  { id: "one-focus", label: "1 格 · 满页", rects: [rect(0.03, 0.03, 0.94, 0.94)] },
  {
    id: "two-beat",
    label: "2 格 · 上下",
    rects: [rect(0.03, 0.03, 0.94, 0.455), rect(0.03, 0.515, 0.94, 0.455)],
  },
  {
    id: "three-rhythm",
    label: "3 格 · 主次",
    rects: [
      rect(0.03, 0.03, 0.94, 0.45),
      rect(0.03, 0.51, 0.455, 0.46),
      rect(0.515, 0.51, 0.455, 0.46),
    ],
  },
  {
    id: "four-grid",
    label: "4 格 · 网格",
    rects: [
      rect(0.03, 0.03, 0.455, 0.455),
      rect(0.515, 0.03, 0.455, 0.455),
      rect(0.03, 0.515, 0.455, 0.455),
      rect(0.515, 0.515, 0.455, 0.455),
    ],
  },
  {
    id: "five-cascade",
    label: "5 格 · 递进",
    rects: [
      rect(0.03, 0.03, 0.94, 0.3),
      rect(0.03, 0.36, 0.455, 0.285),
      rect(0.515, 0.36, 0.455, 0.285),
      rect(0.03, 0.675, 0.455, 0.295),
      rect(0.515, 0.675, 0.455, 0.295),
    ],
  },
  {
    id: "six-grid",
    label: "6 格 · 六宫格",
    rects: [
      rect(0.03, 0.03, 0.455, 0.293),
      rect(0.515, 0.03, 0.455, 0.293),
      rect(0.03, 0.353, 0.455, 0.294),
      rect(0.515, 0.353, 0.455, 0.294),
      rect(0.03, 0.677, 0.455, 0.293),
      rect(0.515, 0.677, 0.455, 0.293),
    ],
  },
];

export function templateCompatibleWithPage(
  template: LayoutTemplate,
  page: StoryboardPageSummary,
): boolean {
  const panelCount = page.panels.length;
  if (!page.page_type || template.rects.length !== panelCount) return false;
  if (panelCount < 1 || panelCount > 6) return false;
  return page.page_type !== "standard" || panelCount >= 3;
}

export function templatesForPage(page: StoryboardPageSummary): LayoutTemplate[] {
  return LAYOUT_TEMPLATES.filter((template) => templateCompatibleWithPage(template, page));
}

export function createLayoutDraft(
  page: StoryboardPageSummary,
  template: LayoutTemplate,
  profile: PageProfile = "print_portrait_2_3",
): PageLayoutDraft {
  if (!templateCompatibleWithPage(template, page)) {
    throw new Error("版式模板与 Storyboard 页型或分镜数量不兼容。");
  }
  const canvas = profile === "vertical_strip" ? { width: 1536, height: 4096 } : { width: 2048, height: 3072 };
  const rootId = crypto.randomUUID();
  const frames: FrameSpec[] = [
    {
      frame_id: rootId,
      parent_frame_id: null,
      panel_id: null,
      order: null,
      rect: rect(0, 0, 1, 1),
      aspect_ratio: canvas.width / canvas.height,
      shot_scale: "establishing",
      focal_point: { x: 0.5, y: 0.5 },
      character_positions: [],
      text_safe_zones: [],
      crop_safe_rect: rect(0, 0, 1, 1),
    },
    ...template.rects.map((frameRect, index): FrameSpec => ({
      frame_id: crypto.randomUUID(),
      parent_frame_id: rootId,
      panel_id: page.panels[index]?.panel_id ?? null,
      order: index + 1,
      rect: frameRect,
      aspect_ratio: frameAspect(frameRect, canvas.width, canvas.height),
      shot_scale: shotScale(page.panels[index]?.order ?? index + 1),
      focal_point: { x: 0.5, y: 0.5 },
      character_positions: (page.panels[index]?.characters ?? []).map((characterId, characterIndex, all) => ({
        character_id: uuidFromText(characterId),
        center: { x: (characterIndex + 1) / (all.length + 1), y: 0.58 },
        prominence: characterIndex === 0 ? "primary" : "secondary",
      })),
      text_safe_zones: [
        {
          zone_id: crypto.randomUUID(),
          kind: "dialogue",
          rect: rect(0.58, 0.05, 0.36, 0.25),
        },
      ],
      crop_safe_rect: rect(0.06, 0.06, 0.88, 0.88),
    })),
  ];
  return {
    schema_version: "1.0",
    page_layout_draft_id: crypto.randomUUID(),
    version: 1,
    page_id: page.page_id,
    page_profile: profile,
    canvas,
    reading_direction: "ltr_ttb",
    frames,
    content_sha256: "0".repeat(64),
    approved_content_sha256: null,
  };
}

export function applyTemplate(
  draft: PageLayoutDraft,
  page: StoryboardPageSummary,
  template: LayoutTemplate,
): PageLayoutDraft {
  const rebuilt = createLayoutDraft(page, template, draft.page_profile);
  return {
    ...rebuilt,
    page_layout_draft_id: draft.page_layout_draft_id,
    version: draft.version,
    reading_direction: draft.reading_direction,
  };
}

export function updateFrame(
  draft: PageLayoutDraft,
  frameId: string,
  patch: Partial<FrameSpec>,
): PageLayoutDraft {
  return recalculateAspectRatios({
    ...draft,
    approved_content_sha256: null,
    frames: draft.frames.map((frame) => {
      if (frame.frame_id !== frameId) return frame;
      const next = { ...frame, ...patch };
      return { ...next, rect: clampRect(next.rect) };
    }),
  });
}

export function changePageProfile(
  draft: PageLayoutDraft,
  profile: PageProfile,
): PageLayoutDraft {
  const canvas =
    profile === "vertical_strip"
      ? { width: 1536, height: 4096 }
      : { width: 2048, height: 3072 };
  return recalculateAspectRatios({
    ...draft,
    page_profile: profile,
    canvas,
    approved_content_sha256: null,
  });
}

export function updateFrameAbsoluteRect(
  draft: PageLayoutDraft,
  frameId: string,
  absoluteRect: NormalizedRect,
): PageLayoutDraft {
  const frame = draft.frames.find((candidate) => candidate.frame_id === frameId);
  if (!frame) return draft;
  const nextRect = frame.parent_frame_id
    ? relativeRect(absoluteRect, absoluteFrameRect(draft, frame.parent_frame_id))
    : absoluteRect;
  return updateFrame(draft, frameId, { rect: clampRect(nextRect) });
}

export function moveReadingOrder(
  draft: PageLayoutDraft,
  frameId: string,
  delta: -1 | 1,
): PageLayoutDraft {
  const leaves = leafFrames(draft);
  const index = leaves.findIndex((frame) => frame.frame_id === frameId);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= leaves.length) return draft;
  const orders = new Map<string, number>();
  const reordered = [...leaves];
  [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
  reordered.forEach((frame, order) => orders.set(frame.frame_id, order + 1));
  return {
    ...draft,
    approved_content_sha256: null,
    frames: draft.frames.map((frame) =>
      orders.has(frame.frame_id) ? { ...frame, order: orders.get(frame.frame_id) ?? null } : frame,
    ),
  };
}

export function splitFrame(
  draft: PageLayoutDraft,
  frameId: string,
  orientation: "horizontal" | "vertical",
): PageLayoutDraft {
  const parent = draft.frames.find((candidate) => candidate.frame_id === frameId);
  const children = draft.frames.filter((candidate) => candidate.parent_frame_id === frameId);
  if (!parent || parent.panel_id !== null || children.length < 2) return draft;

  const axis = orientation === "horizontal" ? "y" : "x";
  const ordered = [...children].sort((first, second) => {
    const firstCenter = first.rect[axis] + first.rect[axis === "x" ? "width" : "height"] / 2;
    const secondCenter = second.rect[axis] + second.rect[axis === "x" ? "width" : "height"] / 2;
    return firstCenter - secondCenter || (first.order ?? 0) - (second.order ?? 0);
  });
  const splitAt = Math.ceil(ordered.length / 2);
  const groups = [ordered.slice(0, splitAt), ordered.slice(splitAt)];
  if (groups.some((group) => group.length === 0)) return draft;

  const groupIds = groups.map(() => crypto.randomUUID());
  const groupRects = groups.map(groupBounds);
  const childToGroup = new Map<string, { groupId: string; bounds: NormalizedRect }>();
  groups.forEach((group, index) => {
    for (const child of group) {
      childToGroup.set(child.frame_id, { groupId: groupIds[index], bounds: groupRects[index] });
    }
  });
  const next: PageLayoutDraft = {
    ...draft,
    approved_content_sha256: null,
    frames: [
      ...draft.frames.map((candidate) => {
        const group = childToGroup.get(candidate.frame_id);
        if (!group) return candidate;
        return {
          ...candidate,
          parent_frame_id: group.groupId,
          rect: relativeRect(candidate.rect, group.bounds),
        };
      }),
      ...groupIds.map((groupId, index): FrameSpec => ({
        frame_id: groupId,
        parent_frame_id: frameId,
        panel_id: null,
        order: null,
        rect: groupRects[index],
        aspect_ratio: 1,
        shot_scale: parent.shot_scale,
        focal_point: { x: 0.5, y: 0.5 },
        character_positions: [],
        text_safe_zones: [],
        crop_safe_rect: rect(0, 0, 1, 1),
      })),
    ],
  };
  return recalculateAspectRatios(next);
}

export function mergeFrame(draft: PageLayoutDraft, frameId: string): PageLayoutDraft {
  const parent = draft.frames.find((frame) => frame.frame_id === frameId);
  const groups = draft.frames.filter((frame) => frame.parent_frame_id === frameId);
  if (!parent || groups.length !== 2 || groups.some((group) => group.panel_id !== null)) {
    return draft;
  }
  const groupById = new Map(groups.map((group) => [group.frame_id, group]));
  const removedIds = new Set(groupById.keys());
  const next: PageLayoutDraft = {
    ...draft,
    approved_content_sha256: null,
    frames: draft.frames
      .filter((frame) => !removedIds.has(frame.frame_id))
      .map((frame) => {
        if (!frame.parent_frame_id) return frame;
        const group = groupById.get(frame.parent_frame_id);
        if (!group) return frame;
        return {
          ...frame,
          parent_frame_id: frameId,
          rect: composeRect(group.rect, frame.rect),
        };
      }),
  };
  return recalculateAspectRatios(next);
}

export function leafFrames(draft: PageLayoutDraft): FrameSpec[] {
  const parents = new Set(draft.frames.map((frame) => frame.parent_frame_id).filter(Boolean));
  return draft.frames
    .filter((frame) => !parents.has(frame.frame_id))
    .sort((first, second) => (first.order ?? 999) - (second.order ?? 999));
}

export function frameDepth(draft: PageLayoutDraft, frameId: string): number {
  const byId = new Map(draft.frames.map((frame) => [frame.frame_id, frame]));
  let depth = 0;
  let current = byId.get(frameId);
  const visited = new Set<string>();
  while (current?.parent_frame_id) {
    if (visited.has(current.frame_id)) return Number.POSITIVE_INFINITY;
    visited.add(current.frame_id);
    depth += 1;
    current = byId.get(current.parent_frame_id);
  }
  return depth;
}

export function absoluteFrameRect(draft: PageLayoutDraft, frameId: string): NormalizedRect {
  const byId = new Map(draft.frames.map((frame) => [frame.frame_id, frame]));
  const cache = new Map<string, NormalizedRect>();
  const active = new Set<string>();
  const resolve = (currentId: string): NormalizedRect => {
    const cached = cache.get(currentId);
    if (cached) return cached;
    if (active.has(currentId)) throw new Error("frame hierarchy contains a cycle");
    const frame = byId.get(currentId);
    if (!frame) throw new Error(`frame ${currentId} is missing`);
    active.add(currentId);
    const absolute = frame.parent_frame_id
      ? composeRect(resolve(frame.parent_frame_id), frame.rect)
      : frame.rect;
    active.delete(currentId);
    cache.set(currentId, absolute);
    return absolute;
  };
  return resolve(frameId);
}

function recalculateAspectRatios(draft: PageLayoutDraft): PageLayoutDraft {
  return {
    ...draft,
    frames: draft.frames.map((frame) => {
      const absolute = absoluteFrameRect(draft, frame.frame_id);
      return {
        ...frame,
        aspect_ratio: frameAspect(absolute, draft.canvas.width, draft.canvas.height),
      };
    }),
  };
}

function groupBounds(frames: FrameSpec[]): NormalizedRect {
  const x = Math.min(...frames.map((frame) => frame.rect.x));
  const y = Math.min(...frames.map((frame) => frame.rect.y));
  const right = Math.max(...frames.map((frame) => frame.rect.x + frame.rect.width));
  const bottom = Math.max(...frames.map((frame) => frame.rect.y + frame.rect.height));
  return rect(x, y, right - x, bottom - y);
}

function relativeRect(child: NormalizedRect, parent: NormalizedRect): NormalizedRect {
  return rect(
    (child.x - parent.x) / parent.width,
    (child.y - parent.y) / parent.height,
    child.width / parent.width,
    child.height / parent.height,
  );
}

function composeRect(parent: NormalizedRect, child: NormalizedRect): NormalizedRect {
  return rect(
    parent.x + child.x * parent.width,
    parent.y + child.y * parent.height,
    child.width * parent.width,
    child.height * parent.height,
  );
}

function clampRect(value: NormalizedRect): NormalizedRect {
  const width = clamp(value.width, 0.01, 1);
  const height = clamp(value.height, 0.01, 1);
  return rect(
    clamp(value.x, 0, 1 - width),
    clamp(value.y, 0, 1 - height),
    width,
    height,
  );
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function frameAspect(frameRect: NormalizedRect, width: number, height: number): number {
  return (frameRect.width * width) / (frameRect.height * height);
}

function shotScale(order: number): ShotScale {
  return (["establishing", "wide", "medium", "close_up", "medium", "full"] as ShotScale[])[
    Math.max(0, Math.min(5, order - 1))
  ];
}

function uuidFromText(value: string): string {
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    return value;
  }
  const encoded = Array.from(new TextEncoder().encode(value));
  let hex = encoded.map((item) => item.toString(16).padStart(2, "0")).join("");
  hex = (hex + "0".repeat(32)).slice(0, 32);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-7${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}
