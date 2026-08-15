import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { NovelAISettings } from "./NovelAISettings";
import { clearLocalSession, consumeLocalSession } from "./api";

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("saves locally before an explicit non-generating connection test", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    const url = String(path);
    if (url.endsWith("/novelai/capabilities")) {
      return Promise.resolve(jsonResponse(capabilities));
    }
    if (url.endsWith("/novelai/config") && !init?.method) {
      return Promise.resolve(jsonResponse({ error: { message: "未配置" } }, 404));
    }
    if (url.endsWith("/novelai/config") && init?.method === "PUT") {
      return Promise.resolve(jsonResponse(configuration));
    }
    if (url.endsWith("/novelai/connection-test") && init?.method === "POST") {
      return Promise.resolve(
        jsonResponse({
          status: "ok",
          provider: "novelai",
          provider_model_id: "nai-diffusion-4-5-full",
          config_revision: 1,
          suggestion_count: 1,
          subscription: {
            profile_version: "novelai-opus-zero-anlas-2026-08-14.1",
            subscription_active: true,
            subscription_tier: 3,
            is_grace_period: false,
            opus_active: true,
          },
          zero_anlas_ready: true,
          model_supports_zero_anlas: true,
          generated_images: 0,
          last_connection_at: "2026-08-09 12:00:00",
        }),
      );
    }
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <NovelAISettings
      projectId="project-1"
      vaultStatus={{
        configured: true,
        unlocked: true,
        profiles: [
          {
            profile_id: "novelai",
            provider: "novelai",
            label: "NovelAI 图像生成",
            fingerprint: "…cret",
          },
        ],
      }}
      onError={vi.fn()}
    />,
  );

  expect(await screen.findByText(/支持 V4.5 Precise Reference/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /连接测试/ })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "仅保存本地配置" }));
  expect(await screen.findByText(/尚未调用外部接口/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /连接测试/ })).toBeEnabled();

  fireEvent.click(screen.getByRole("button", { name: /连接测试/ }));
  expect(await screen.findByText(/生成图片 0 张/)).toBeInTheDocument();

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  const mutatingRequests = fetchMock.mock.calls.filter(([, init]) => init?.method);
  expect(mutatingRequests.map(([path]) => String(path))).toEqual([
    "/api/v1/projects/project-1/novelai/config",
    "/api/v1/projects/project-1/novelai/connection-test",
  ]);
  expect(mutatingRequests.every(([, init]) => new Headers(init?.headers).has("X-CSRF-Token"))).toBe(true);

  fireEvent.change(screen.getByLabelText("图像模型"), {
    target: { value: "nai-diffusion-3" },
  });
  expect(screen.getByText(/当前结构化 V4 生成链路不支持此模型/)).toBeInTheDocument();
});

const capabilities = {
  source_url: "https://image.novelai.net/docs/doc.json",
  sha256: "f43ea4feff0d390dc65e5ed704d4cf7e75af741bb413b86981f465fb8fb556f8",
  fetched_on: "2026-08-09",
  swagger_version: "2.0",
  api_title: "Omegalaser API",
  api_version: "1.0",
  mapping_version: "novelai-image-2026-08-09.3-v03-opus-zero-anlas-1",
  allowed_paths: {},
  opus_zero_anlas_profile: {
    profile_version: "novelai-opus-zero-anlas-2026-08-14.1",
    required_tier: 3,
    max_pixels: 1_048_576,
    max_steps: 28,
    n_samples: 1,
    requires_single_image: true,
    allows_base_or_reference_image: false,
    default_dimensions: [
      { width: 832, height: 1216 },
      { width: 1216, height: 832 },
      { width: 1024, height: 1024 },
    ],
    official_docs: [],
  },
  models: [
    {
      provider_model_id: "nai-diffusion-4-5-full",
      label: "Anime V4.5 Full",
      inpaint_model_id: "nai-diffusion-4-5-full-inpainting",
      recommended: true,
      supports_opus_zero_anlas: true,
      supports_precise_reference: true,
      supports_multi_character_prompt: true,
      supports_vibe_transfer: true,
      precise_reference_excludes_vibe_transfer: true,
      prompt_token_note: "约 512 T5 tokens",
    },
    {
      provider_model_id: "nai-diffusion-3",
      label: "Anime V3",
      inpaint_model_id: "nai-diffusion-3-inpainting",
      recommended: false,
      supports_opus_zero_anlas: false,
      supports_precise_reference: false,
      supports_multi_character_prompt: false,
      supports_vibe_transfer: true,
      precise_reference_excludes_vibe_transfer: false,
      prompt_token_note: "旧版模型",
    },
  ],
};

const configuration = {
  project_id: "project-1",
  provider: "novelai",
  model_label: "Anime V4.5 Full",
  provider_model_id: "nai-diffusion-4-5-full",
  inpaint_model_id: "nai-diffusion-4-5-full-inpainting",
  credential_profile_id: "novelai",
  credential_fingerprint: "…cret",
  credential_status: "available",
  timeout_seconds: 30,
  contract_sha256: capabilities.sha256,
  mapping_version: capabilities.mapping_version,
  revision: 1,
  last_connection_status: null,
  last_connection_at: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
