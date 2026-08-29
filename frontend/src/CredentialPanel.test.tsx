import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { CredentialPanel } from "./CredentialPanel";
import {
  type TextModelConfiguration,
  type VaultStatus,
  clearLocalSession,
  consumeLocalSession,
} from "./api";

afterEach(() => {
  cleanup();
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("renders the project text model configuration as exactly four user-facing fields", () => {
  render(
    <CredentialPanel
      status={{ configured: true, unlocked: true, profiles: [] }}
      onStatusChange={vi.fn()}
    />,
  );

  expect(screen.getByLabelText("备注名称（可选）")).toBeInTheDocument();
  expect(screen.getByLabelText("URL")).toBeInTheDocument();
  expect(screen.getByLabelText("Key/Password")).toBeInTheDocument();
  expect(screen.getByLabelText("Request Model")).toBeInTheDocument();
  expect(screen.queryByLabelText("凭证类型")).not.toBeInTheDocument();
  expect(screen.queryByText("凭证标识")).not.toBeInTheDocument();
  expect(screen.queryByText("显示名称")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("API 密钥")).not.toBeInTheDocument();
});

it("saves the four-field text model configuration with a local Key/Password", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/adaptation/text-model") && method === "GET") {
      return Promise.resolve(jsonResponse({ error: { message: "尚未配置" } }, 404));
    }
    if (path.endsWith("/adaptation/text-model") && method === "PUT") {
      return Promise.resolve(
        jsonResponse(
          textModelConfiguration({
            remark_name: "主力分镜模型",
            credential_profile_id: "text-model-project-1",
          }),
        ),
      );
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  const onTextModelSaved = vi.fn();

  render(
    <CredentialPanel
      status={{ configured: true, unlocked: true, profiles: [] }}
      onStatusChange={vi.fn()}
      projectId="project-1"
      onTextModelSaved={onTextModelSaved}
    />,
  );

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText("备注名称（可选）"), {
    target: { value: "主力分镜模型" },
  });
  fireEvent.change(screen.getByLabelText("URL"), {
    target: { value: "https://models.example.test/v1" },
  });
  fireEvent.change(screen.getByLabelText("Key/Password"), {
    target: { value: "unit-secret-value" },
  });
  fireEvent.change(screen.getByLabelText("Request Model"), {
    target: { value: "unit-model" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存文本大模型配置" }));

  expect(await screen.findByText(/文本大模型配置已加密保存在本机/)).toBeInTheDocument();
  const put = fetchMock.mock.calls.find(
    ([path, init]) =>
      String(path).endsWith("/adaptation/text-model") && init?.method === "PUT",
  );
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({
    remark_name: "主力分镜模型",
    url: "https://models.example.test/v1",
    key_password: "unit-secret-value",
    request_model: "unit-model",
  });
  expect(screen.getByLabelText("Key/Password")).toHaveValue("");
  expect(document.body.textContent).not.toContain("unit-secret-value");
  expect(onTextModelSaved).toHaveBeenCalledTimes(1);
});

it("clears the previous project configuration while the next project loads", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  let resolveProjectTwo: ((response: Response) => void) | undefined;
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const path = String(input);
    if (path.includes("/project-1/")) {
      return Promise.resolve(
        jsonResponse(
          textModelConfiguration({
            project_id: "project-1",
            remark_name: "项目一模型",
          }),
        ),
      );
    }
    if (path.includes("/project-2/")) {
      return new Promise<Response>((resolve) => {
        resolveProjectTwo = resolve;
      });
    }
    return Promise.reject(new Error(`unexpected request: ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  const status = { configured: true, unlocked: true, profiles: [] };
  const { rerender } = render(
    <CredentialPanel status={status} onStatusChange={vi.fn()} projectId="project-1" />,
  );
  expect(await screen.findByText(/项目一模型/)).toBeInTheDocument();

  rerender(
    <CredentialPanel status={status} onStatusChange={vi.fn()} projectId="project-2" />,
  );

  expect(screen.queryByText(/项目一模型/)).not.toBeInTheDocument();
  expect(screen.getByText("正在读取当前项目的文本模型配置…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存文本大模型配置" })).toBeDisabled();

  await act(async () => {
    resolveProjectTwo?.(
      jsonResponse(
        textModelConfiguration({
          project_id: "project-2",
          remark_name: "项目二模型",
          revision: 2,
        }),
      ),
    );
  });
  expect(await screen.findByText(/项目二模型/)).toBeInTheDocument();
  expect(screen.queryByText("正在读取当前项目的文本模型配置…")).not.toBeInTheDocument();
});

it("updates text model metadata without resending the saved Key/Password", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/adaptation/text-model") && method === "GET") {
      return Promise.resolve(
        jsonResponse(textModelConfiguration({ remark_name: "主力分镜模型" })),
      );
    }
    if (path.endsWith("/adaptation/text-model") && method === "PUT") {
      return Promise.resolve(
        jsonResponse(
          textModelConfiguration({
            remark_name: "备用分镜模型",
            request_model: "updated-model",
            model_name: "updated-model",
            model: "updated-model",
            revision: 2,
          }),
        ),
      );
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <CredentialPanel
      status={{ configured: true, unlocked: true, profiles: [] }}
      onStatusChange={vi.fn()}
      projectId="project-1"
    />,
  );

  const remarkName = await screen.findByDisplayValue("主力分镜模型");
  fireEvent.change(remarkName, { target: { value: "备用分镜模型" } });
  fireEvent.change(screen.getByLabelText("Request Model"), {
    target: { value: "updated-model" },
  });
  expect(screen.getByLabelText("Key/Password")).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "保存文本大模型配置" }));

  expect(await screen.findByText(/备用分镜模型/)).toBeInTheDocument();
  const put = fetchMock.mock.calls.find(
    ([path, init]) =>
      String(path).endsWith("/adaptation/text-model") && init?.method === "PUT",
  );
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({
    remark_name: "备用分镜模型",
    url: "https://models.example.test/v1",
    request_model: "updated-model",
  });
});

it("creates the local vault without exposing the master password", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    if (String(path) === "/api/v1/vault" && init?.method === "POST") {
      return Promise.resolve(
        jsonResponse({ configured: true, unlocked: true, profiles: [] }, 201),
      );
    }
    return Promise.reject(new Error(`unexpected request: ${String(path)}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<VaultHarness />);
  fireEvent.change(screen.getByLabelText("设置主密码（至少 10 个字符）"), {
    target: { value: "unit test master password" },
  });
  fireEvent.change(screen.getByLabelText("再次输入主密码"), {
    target: { value: "unit test master password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建本地凭证库" }));

  expect(await screen.findByText(/凭证库已创建并解锁/)).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("unit test master password");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  for (const [, init] of fetchMock.mock.calls) {
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Manga-Maker-Session")).toBe("session-test");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
  }
});

it("saves a NovelAI token under the dedicated local provider type", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((_path: RequestInfo | URL, init?: RequestInit) =>
    Promise.resolve(
      jsonResponse({
        profile_id: "novelai",
        provider: "novelai",
        label: "NovelAI 图像生成",
        fingerprint: "…cret",
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  render(
    <CredentialPanel
      status={{ configured: true, unlocked: true, profiles: [] }}
      onStatusChange={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("NovelAI Token"), {
    target: { value: "unit-local-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "加密保存 NovelAI 凭证" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [, init] = fetchMock.mock.calls[0];
  expect(JSON.parse(String(init?.body))).toMatchObject({ provider: "novelai" });
  expect(document.body.textContent).not.toContain("unit-local-secret");
});

it("renders structured FastAPI validation errors as readable text", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        jsonResponse(
          {
            detail: [
              {
                type: "string_too_short",
                loc: ["body", "master_password"],
                msg: "String should have at least 10 characters",
              },
            ],
          },
          422,
        ),
      ),
    ),
  );

  render(<VaultHarness />);
  fireEvent.change(screen.getByLabelText("设置主密码（至少 10 个字符）"), {
    target: { value: "long-enough-password" },
  });
  fireEvent.change(screen.getByLabelText("再次输入主密码"), {
    target: { value: "long-enough-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建本地凭证库" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("body.master_password");
  expect(alert).toHaveTextContent("String should have at least 10 characters");
  expect(alert).not.toHaveTextContent("[object Object]");
});

function VaultHarness() {
  const [status, setStatus] = useState<VaultStatus>({
    configured: false,
    unlocked: false,
    profiles: [],
  });
  return <CredentialPanel status={status} onStatusChange={setStatus} />;
}

function textModelConfiguration(
  overrides: Partial<TextModelConfiguration> = {},
): TextModelConfiguration {
  return {
    project_id: "project-1",
    text_model_profile_id: "project-1",
    provider: "openai-compatible",
    remark_name: null,
    url: "https://models.example.test/v1",
    provider_api_url: "https://models.example.test/v1",
    base_url: "https://models.example.test/v1",
    endpoint_host: "models.example.test",
    request_model: "unit-model",
    model_name: "unit-model",
    model: "unit-model",
    credential_profile_id: "text-model",
    credential_fingerprint: "…alue",
    credential_status: "available",
    timeout_seconds: 60,
    temperature: 0.2,
    revision: 1,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
