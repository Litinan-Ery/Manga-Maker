export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  environment: string;
  database: "ok" | "error";
  schema_version: number;
  vault_configured: boolean;
  vault_unlocked: boolean;
}

export interface Project {
  project_id: string;
  title: string;
  status: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface EncodingCandidate {
  encoding: string;
  confidence: number;
  preview: string;
  cjk_ratio: number;
}

export interface SourcePreflight {
  preflight_id: string;
  filename: string;
  byte_size: number;
  sha256: string;
  candidates: EncodingCandidate[];
  recommended_encoding: string;
  requires_confirmation: boolean;
}

export interface Chapter {
  chapter_id: string;
  version: number;
  ordinal: number;
  title: string;
  start_offset: number;
  end_offset: number;
  text_sha256: string;
}

export interface ChapterSet {
  source_file_id: string;
  chapter_set_id: string;
  chapter_set_version: number;
  chapters: Chapter[];
}

export interface ChapterText {
  chapter_id: string;
  title: string;
  start_offset: number;
  end_offset: number;
  text: string;
}

export interface ChapterBoundaryInput {
  title: string;
  start_offset: number;
  end_offset: number;
}

export interface StoryBeat {
  beat_id: string;
  ordinal: number;
  anchor_id: string;
  source_summary: string;
  source_excerpt: string;
  start_offset: number;
  end_offset: number;
  excerpt_sha256: string;
  resolution_status: "represented" | "condensed" | "omitted" | "unresolved";
  omission_reason: string | null;
}

export interface StoryBeatSet {
  beat_set_id: string;
  beat_set_version: number;
  chapter_id: string;
  beats: StoryBeat[];
}

interface ErrorPayload {
  error?: { message?: string };
  detail?: string;
}

let localSessionToken: string | null = null;
let localCsrfToken: string | null = null;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function consumeLocalSession(): boolean {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const session = params.get("session");
  const csrf = params.get("csrf");
  if (session && csrf) {
    localSessionToken = session;
    localCsrfToken = csrf;
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }
  return localSessionToken !== null && localCsrfToken !== null;
}

export function clearLocalSession(): void {
  localSessionToken = null;
  localCsrfToken = null;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { signal }, false);
}

export function listProjects(signal?: AbortSignal): Promise<Project[]> {
  return request<Project[]>("/api/v1/projects", { signal }, false);
}

export function createProject(title: string): Promise<Project> {
  return request<Project>(
    "/api/v1/projects",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    },
    true,
  );
}

export function preflightSource(projectId: string, file: File): Promise<SourcePreflight> {
  const body = new FormData();
  body.append("file", file);
  return request<SourcePreflight>(
    `/api/v1/projects/${projectId}/source/preflight`,
    { method: "POST", body },
    true,
  );
}

export function confirmSource(
  projectId: string,
  preflightId: string,
  encoding: string,
): Promise<ChapterSet> {
  return request<ChapterSet>(
    `/api/v1/projects/${projectId}/source/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preflight_id: preflightId, encoding }),
    },
    true,
  );
}

export function getChapters(projectId: string, signal?: AbortSignal): Promise<ChapterSet> {
  return request<ChapterSet>(`/api/v1/projects/${projectId}/source/chapters`, { signal }, false);
}

export function getChapterText(projectId: string, chapterId: string): Promise<ChapterText> {
  return request<ChapterText>(
    `/api/v1/projects/${projectId}/source/chapters/${chapterId}/text`,
    {},
    false,
  );
}

export function replaceChapters(
  projectId: string,
  sourceFileId: string,
  chapters: ChapterBoundaryInput[],
): Promise<ChapterSet> {
  return request<ChapterSet>(
    `/api/v1/projects/${projectId}/source/chapters`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_file_id: sourceFileId, chapters }),
    },
    true,
  );
}

export function getStoryBeats(projectId: string, chapterId: string): Promise<StoryBeatSet> {
  return request<StoryBeatSet>(
    `/api/v1/projects/${projectId}/source/chapters/${chapterId}/story-beats`,
    {},
    false,
  );
}

export function draftStoryBeats(projectId: string, chapterId: string): Promise<StoryBeatSet> {
  return request<StoryBeatSet>(
    `/api/v1/projects/${projectId}/source/chapters/${chapterId}/story-beats/draft`,
    { method: "POST" },
    true,
  );
}

async function request<T>(path: string, init: RequestInit, needsSession: boolean): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (needsSession) {
    if (!localSessionToken || !localCsrfToken) {
      throw new ApiError("本地会话已失效，请重新运行 Manga Maker 启动器。", 401);
    }
    headers.set("X-Manga-Maker-Session", localSessionToken);
    headers.set("X-CSRF-Token", localCsrfToken);
  }

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("无法连接本地 Manga Maker 服务。请确认启动器仍在运行。");
  }
  if (!response.ok) {
    let payload: ErrorPayload = {};
    try {
      payload = (await response.json()) as ErrorPayload;
    } catch {
      // Keep the safe generic message when a proxy or server returns non-JSON.
    }
    throw new ApiError(
      payload.error?.message ?? payload.detail ?? "本地服务暂时无法完成该操作。",
      response.status,
    );
  }
  return (await response.json()) as T;
}
