import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { CredentialPanel } from "./CredentialPanel";
import { type VaultStatus, clearLocalSession, consumeLocalSession } from "./api";

afterEach(() => {
  cleanup();
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("creates the local vault and clears credential inputs after encrypted save", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    if (String(path) === "/api/v1/vault" && init?.method === "POST") {
      return Promise.resolve(
        jsonResponse({ configured: true, unlocked: true, profiles: [] }, 201),
      );
    }
    if (String(path) === "/api/v1/vault/profiles/text-model" && init?.method === "PUT") {
      return Promise.resolve(
        jsonResponse({
          profile_id: "text-model",
          provider: "openai-compatible",
          label: "文本模型",
          fingerprint: "…alue",
        }),
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
  fireEvent.change(screen.getByLabelText("API 密钥"), {
    target: { value: "unit-credential-value" },
  });
  fireEvent.click(screen.getByRole("button", { name: "加密保存凭证" }));

  expect(await screen.findByText(/界面只保留指纹/)).toBeInTheDocument();
  expect(screen.getByLabelText("API 密钥")).toHaveValue("");
  expect(document.body.textContent).not.toContain("unit-credential-value");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
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
  fireEvent.change(screen.getByLabelText("凭证类型"), { target: { value: "novelai" } });
  expect(screen.getByLabelText("凭证标识")).toHaveValue("novelai");
  fireEvent.change(screen.getByLabelText("API 密钥"), { target: { value: "unit-local-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "加密保存凭证" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [, init] = fetchMock.mock.calls[0];
  expect(JSON.parse(String(init?.body))).toMatchObject({ provider: "novelai" });
  expect(document.body.textContent).not.toContain("unit-local-secret");
});

function VaultHarness() {
  const [status, setStatus] = useState<VaultStatus>({
    configured: false,
    unlocked: false,
    profiles: [],
  });
  return <CredentialPanel status={status} onStatusChange={setStatus} />;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
