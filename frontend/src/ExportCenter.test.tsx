import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ExportCenter } from "./ExportCenter";
import { clearLocalSession, consumeLocalSession } from "./api";

afterEach(() => {
  cleanup();
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("requires confirmation for immutable export and package restore", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/projects/project-1/exports") && method === "GET") {
      return Promise.resolve(jsonResponse([]));
    }
    if (path.endsWith("/exports/preflight") && method === "POST") {
      return Promise.resolve(jsonResponse(exportPlan));
    }
    if (path.endsWith("/projects/project-1/exports") && method === "POST") {
      return Promise.resolve(jsonResponse(exportRevision, 201));
    }
    if (path === "/api/v1/imports/preflight" && method === "POST") {
      return Promise.resolve(jsonResponse(importPlan, 201));
    }
    if (path.endsWith("/imports/import-1/restore") && method === "POST") {
      return Promise.resolve(
        jsonResponse({
          import_preflight_id: "import-1",
          project_id: "project-restored",
          source_project_id: "project-1",
          title: "测试漫画 (恢复)",
          id_conflict_remapped: true,
          record_counts: { projects: 1 },
          file_count: 12,
          external_requests_started: 0,
        }),
      );
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  const { container } = render(
    <ExportCenter projectId="project-1" chapterSet={chapterSet} onError={vi.fn()} />,
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

  fireEvent.click(screen.getByRole("button", { name: "预检并冻结页面版本" }));
  expect(await screen.findByText(/1 页 · 工程包/)).toBeInTheDocument();
  const exportButton = screen.getByRole("button", { name: "生成并校验四种格式" });
  expect(exportButton).toBeDisabled();
  expect(
    fetchMock.mock.calls.filter(
      ([path, init]) =>
        String(path).endsWith("/projects/project-1/exports") && init?.method === "POST",
    ),
  ).toHaveLength(0);

  fireEvent.click(screen.getByLabelText(/我确认以上页面版本和顺序/));
  fireEvent.click(exportButton);
  expect(await screen.findByText(/全部校验并发布/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "下载 工程包" })).toBeInTheDocument();

  const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
  if (!fileInput) throw new Error("missing package file input");
  fireEvent.change(fileInput, {
    target: {
      files: [new File(["zip"], "project.manga-maker.zip", { type: "application/zip" })],
    },
  });
  expect(await screen.findByText(/dry-run 通过/)).toBeInTheDocument();
  const restoreButton = screen.getByRole("button", { name: "确认恢复为新项目" });
  expect(restoreButton).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/任何 ID 冲突都不得覆盖/));
  fireEvent.click(restoreButton);
  expect(await screen.findByText(/原项目 ID 冲突已安全重映射/)).toBeInTheDocument();

  const mutatingCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  for (const [, init] of mutatingCalls) {
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Manga-Maker-Session")).toBe("session-test");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
  }
});

const chapterSet = {
  source_file_id: "source-1",
  chapter_set_id: "chapter-set-1",
  chapter_set_version: 1,
  chapters: [
    {
      chapter_id: "chapter-1",
      version: 1,
      ordinal: 1,
      title: "第一章",
      start_offset: 0,
      end_offset: 20,
      text_sha256: "a".repeat(64),
    },
  ],
};

const selectedPage = {
  ordinal: 1,
  page_id: "page-1",
  page_number: 1,
  page_version_id: "page-version-1",
  version: 1,
  render_sha256: "a".repeat(64),
  width: 2048,
  height: 3072,
};

const exportPlan = {
  project_id: "project-1",
  project_title: "测试漫画",
  chapter_id: "chapter-1",
  chapter_title: "第一章",
  schema_version: "1.0",
  page_count: 1,
  pages: [selectedPage],
  blockers: [],
  warnings: [],
  plan_fingerprint: "b".repeat(64),
  formats: ["engineering_package", "png", "pdf", "cbz"],
  external_requests_started: 0,
};

const exportRevision = {
  export_revision_id: "export-1",
  project_id: "project-1",
  chapter_id: "chapter-1",
  chapter_title: "第一章",
  status: "completed",
  schema_version: "1.0",
  pages: [selectedPage],
  selection_sha256: "b".repeat(64),
  failure_code: null,
  created_at: "2026-08-09 12:00:00",
  completed_at: "2026-08-09 12:00:01",
  files: [
    {
      export_file_id: "file-package",
      kind: "engineering_package",
      ordinal: null,
      filename: "project.manga-maker.zip",
      sha256: "c".repeat(64),
      byte_size: 100,
    },
  ],
  external_requests_started: 0,
};

const importPlan = {
  import_preflight_id: "import-1",
  filename: "project.manga-maker.zip",
  package_sha256: "d".repeat(64),
  source_project_id: "project-1",
  source_title: "测试漫画",
  schema_version: "1.0",
  file_count: 12,
  expanded_bytes: 2048,
  record_counts: { projects: 1 },
  page_count: 1,
  requires_confirmation: true,
  writes_performed: 0,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
