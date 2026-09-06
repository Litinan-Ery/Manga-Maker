import { ApiError, getLocalSessionCredentials, request, type ComicPageVersion } from "./api";

export interface FullPageSource {
  page_id: string;
  page_number: number;
  panels: { panel_id: string; visual_description: string; texts: string[]; characters?: string[] }[];
}
export interface FullPageOptions {
  chapter_id: string;
  page_number: number;
  text_policy: "local" | "model";
  seed: number;
  cost_ceiling_anlas: number;
  panel_descriptions: Record<string, string>;
  panel_texts: Record<string, string[]>;
  panel_characters?: Record<string, string[]>;
}
export interface FullPageGeneration {
  generation_id: string;
  page_id: string;
  page_number: number;
  panel_count: number;
  plan_sha256: string;
  status: "draft" | "running" | "ready" | "failed" | "needs_review";
  options: FullPageOptions;
  provider_payload: { input: string; model: string; parameters: {
    negative_prompt: string; width: number; height: number;
    qualityToggle: boolean; tag_hint_qt: number; tag_hint_uc_preset: number;
  } };
  generation_calls: number;
  verification_calls: number;
  cost_ceiling_anlas: number;
  external_requests_started: number;
  removed_conflicting_tags: string[];
  error_code: string | null;
  created_at: string;
}
const base = (projectId: string) => `/api/v1/projects/${projectId}/full-pages`;
const post = (body: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
export function fullPageSources(projectId: string, chapterId: string) {
  return request<{ pages: FullPageSource[] }>(`${base(projectId)}/sources?chapter_id=${chapterId}`, {}, false);
}
export function fullPageHistory(projectId: string, chapterId: string) {
  return request<FullPageGeneration[]>(`${base(projectId)}?chapter_id=${chapterId}`, {}, false);
}
export function previewFullPage(projectId: string, options: FullPageOptions) {
  return request<FullPageGeneration>(`${base(projectId)}/preview`, post(options), true);
}
export function generateFullPage(projectId: string, plan: FullPageGeneration) {
  return request<FullPageGeneration>(`${base(projectId)}/${plan.generation_id}/generate`, post({
    plan_sha256: plan.plan_sha256, confirmed: true,
  }), true);
}
export function adoptFullPage(projectId: string, generationId: string, expectedRevision: number) {
  return request<ComicPageVersion>(`${base(projectId)}/${generationId}/adopt`, post({
    expected_revision: expectedRevision, confirmed: true,
  }), true);
}
export async function fullPageImage(projectId: string, generationId: string): Promise<Blob> {
  const credentials = getLocalSessionCredentials();
  if (!credentials) throw new ApiError("请重新打开本地应用以恢复会话。", 401);
  const response = await fetch(`${base(projectId)}/${generationId}/content`, { headers: {
    "X-Manga-Maker-Session": credentials.session, "X-CSRF-Token": credentials.csrf,
  } });
  if (!response.ok) throw new ApiError("无法读取整页成图。", response.status);
  return response.blob();
}
