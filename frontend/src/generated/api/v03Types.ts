/** Generated boundary for contracts/schemas/v0.3. Do not add UI behavior here. */

export interface NormalizedPoint {
  x: number;
  y: number;
}

export interface NormalizedRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type PageProfile = "print_portrait_2_3" | "digital_portrait_2_3" | "vertical_strip";
export type ReadingDirection = "ltr_ttb" | "rtl_ttb" | "ttb";
export type ShotScale =
  | "extreme_close_up"
  | "close_up"
  | "medium"
  | "full"
  | "wide"
  | "establishing";

export interface CharacterPosition {
  character_id: string;
  center: NormalizedPoint;
  prominence: "primary" | "secondary" | "background";
}

export interface TextSafeZone {
  zone_id: string;
  kind: "dialogue" | "narration" | "sfx" | "any";
  rect: NormalizedRect;
}

export interface FrameSpec {
  frame_id: string;
  parent_frame_id: string | null;
  panel_id: string | null;
  order: number | null;
  rect: NormalizedRect;
  aspect_ratio: number;
  shot_scale: ShotScale;
  focal_point: NormalizedPoint;
  character_positions: CharacterPosition[];
  text_safe_zones: TextSafeZone[];
  crop_safe_rect: NormalizedRect;
}

export interface PageLayoutDraft {
  schema_version: "1.0";
  page_layout_draft_id: string;
  version: number;
  page_id: string;
  page_profile: PageProfile;
  canvas: { width: number; height: number };
  reading_direction: ReadingDirection;
  frames: FrameSpec[];
  content_sha256: string;
  approved_content_sha256: string | null;
}

export interface ContractErrorPayload {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
