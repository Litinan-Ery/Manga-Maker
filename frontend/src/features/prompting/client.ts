import { ContractApiError } from "../../generated/api/contractError";

export interface PromptInspectorCharacter {
  character_id: string;
  character_tag_set_version_id: string;
  fixed_tags: string[];
  fixed_tags_sha256: string;
  variable_positive_tags: string[];
  negative_tags: string[];
  action: string;
  order: number;
  center: { x: number; y: number };
}

export interface PromptInspectorPlan {
  schema_version: "2.0";
  prompt_plan_id: string;
  version: number;
  panel_id: string;
  base: {
    positive_tags: string[];
    negative_tags: string[];
    relationship_action: string | null;
  };
  characters: PromptInspectorCharacter[];
  style_tags: string[];
  continuity_tags: string[];
  layout_constraints: Record<string, unknown>;
  content_sha256: string;
}

export interface PromptInspectorPanel {
  panel_id: string;
  prompt_package_id: string;
  prompt_package_sha256: string;
  prompt_plan: PromptInspectorPlan;
  prompt_plan_sha256: string;
  provider_execution_spec: {
    action: "generate" | "infill";
    base_positive_tags: string[];
    base_negative_tags: string[];
    character_captions: Array<{
      character_id: string;
      order: number;
      center: { x: number; y: number };
      positive_tags: string[];
      negative_tags: string[];
    }>;
    [key: string]: unknown;
  };
  provider_execution_spec_sha256: string;
  provider_payload_sha256: string;
  provider_payload: Record<string, unknown>;
  mapping_version: string;
  model_id: string;
}

export interface PromptInspectorImpact {
  artifact: {
    artifact_type: string;
    artifact_id: string;
    version: number;
    content_sha256: string;
    is_stale: boolean;
  };
  path: Array<{
    artifact: { artifact_type: string; artifact_id: string; version: number };
    via_edge_type: string | null;
  }>;
  marked_stale: boolean;
}

export interface PromptInspector {
  contract_version: "1.0";
  prompt_bundle_version_id: string;
  snapshot_sha256: string;
  panels: PromptInspectorPanel[];
  impact: {
    impacts: PromptInspectorImpact[];
    requires_reestimate: boolean;
  };
  generation_summary: {
    panel_count: number;
    candidate_count_per_panel: number | null;
    estimated_calls: number;
    estimated_cost_upper_anlas: null;
    cost_status: "requires_generation_estimate";
    cost_notice: string;
  };
  redaction: {
    credentials_included: false;
    headers_included: false;
    source_chapter_included: false;
    base64_included: false;
  };
  external_requests_started: 0;
}

export interface PromptInspectorClient {
  inspect(
    projectId: string,
    versionId: string,
    snapshotSha256: string,
  ): Promise<PromptInspector>;
}

export function createPromptInspectorHttpClient(): PromptInspectorClient {
  return {
    inspect(projectId, versionId, snapshotSha256) {
      return request(
        `/api/v1/projects/${projectId}/prompting/prompt-bundles/${versionId}/inspector?` +
          `snapshot_sha256=${encodeURIComponent(snapshotSha256)}`,
      );
    },
  };
}

async function request(path: string): Promise<PromptInspector> {
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" } });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ContractApiError(0, {
      error: { code: "LOCAL_SERVICE_UNREACHABLE", message: "无法连接本地 Manga Maker 服务。" },
    });
  }
  if (!response.ok) {
    const payload = await safePayload(response);
    throw new ContractApiError(response.status, {
      error: {
        code: payload.error?.code ?? `HTTP_${response.status}`,
        message: payload.error?.message ?? "Prompt Inspector 暂时无法加载。",
        details: payload.error?.details,
      },
    });
  }
  return (await response.json()) as PromptInspector;
}

async function safePayload(response: Response): Promise<{
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}> {
  try {
    return (await response.json()) as {
      error?: { code?: string; message?: string; details?: Record<string, unknown> };
    };
  } catch {
    return {};
  }
}
