import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { clearLocalSession } from "./api";

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("shows local component status without exposing secrets", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "ok",
            version: "0.1.0",
            environment: "test",
            database: "ok",
            schema_version: 1,
            vault_configured: true,
            vault_unlocked: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(<App />);

    expect(await screen.findByText("后端连接正常")).toBeInTheDocument();
    expect(screen.getByText("已配置")).toBeInTheDocument();
    expect(screen.getByText(/请从 Manga Maker 启动器打开/)).toBeInTheDocument();
    expect(document.body.textContent?.toLowerCase()).not.toContain("secret");
  });

  it("shows an actionable message when the backend is offline", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

    render(<App />);

    expect(await screen.findByText("本地服务未连接")).toBeInTheDocument();
    expect(screen.getByText(/请确认启动器仍在运行/)).toBeInTheDocument();
  });

  it("consumes the launch fragment and creates a local project with protected headers", async () => {
    window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path === "/health") {
        return Promise.resolve(
          jsonResponse({
          status: "ok",
          version: "0.1.0",
          environment: "test",
          database: "ok",
          schema_version: 2,
          vault_configured: false,
          vault_unlocked: false,
          }),
        );
      }
      if (path === "/api/v1/projects" && method === "GET") {
        return Promise.resolve(jsonResponse([]));
      }
      if (path === "/api/v1/vault" && method === "GET") {
        return Promise.resolve(
          jsonResponse({ configured: false, unlocked: false, profiles: [] }),
        );
      }
      if (path === "/api/v1/system/recovery" && method === "GET") {
        return Promise.resolve(
          jsonResponse({
            recovery_run_id: "recovery-1",
            trigger: "startup",
            status: "healthy",
            integrity: { critical_findings: 0, staging_items: 0 },
            external_requests_started: 0,
          }),
        );
      }
      if (path === "/api/v1/projects" && method === "POST") {
        return Promise.resolve(
          jsonResponse(
          {
            project_id: "project-1",
            title: "雨夜侦探",
            status: "draft",
            revision: 1,
            created_at: "2026-08-09T00:00:00Z",
            updated_at: "2026-08-09T00:00:00Z",
          },
          201,
          ),
        );
      }
      if (path.endsWith("/source/chapters")) {
        return Promise.resolve(jsonResponse({ error: { message: "尚未导入" } }, 404));
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    expect(await screen.findByText("创建或选择项目")).toBeInTheDocument();
    expect(window.location.hash).toBe("");

    fireEvent.change(screen.getByLabelText("项目名称"), { target: { value: "雨夜侦探" } });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));

    expect(await screen.findByText("导入 TXT 小说")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6));
    const createCall = fetchMock.mock.calls.find(
      ([path, init]) => String(path) === "/api/v1/projects" && init?.method === "POST",
    );
    expect(createCall).toBeDefined();
    if (!createCall) throw new Error("project creation request was not sent");
    const createInit = createCall?.[1];
    const headers = new Headers(createInit?.headers);
    expect(headers.get("X-Manga-Maker-Session")).toBe("session-test");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
  });
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
