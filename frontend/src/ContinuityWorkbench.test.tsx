import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ContinuityWorkbench } from "./ContinuityWorkbench";
import {
  type ContinuityImpact,
  type ContinuityVersion,
  clearLocalSession,
  consumeLocalSession,
} from "./api";

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("drafts, analyzes, versions and approves cross-chapter state locally", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  let current: ContinuityVersion = ledgerVersion("draft", 1);
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/continuity") && method === "GET") {
      return Promise.resolve(jsonResponse({ error: { message: "尚未建立" } }, 404));
    }
    if (path.endsWith("/continuity/draft") && method === "POST") {
      return Promise.resolve(jsonResponse(current, 201));
    }
    if (path.endsWith("/continuity-version-1/impact") && method === "POST") {
      return Promise.resolve(jsonResponse(impact));
    }
    if (path.endsWith("/continuity-version-1/revisions") && method === "POST") {
      const body = JSON.parse(String(init?.body));
      current = {
        ...ledgerVersion("draft", 2),
        continuity_version_id: "continuity-version-2",
        document: body.document,
        impact,
      };
      return Promise.resolve(jsonResponse(current, 201));
    }
    if (path.endsWith("/continuity-version-2/approve") && method === "POST") {
      current = { ...current, approval_status: "approved" };
      return Promise.resolve(jsonResponse(current));
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <ContinuityWorkbench
      projectId="project-1"
      chapterSet={chapterSet}
      onError={vi.fn()}
    />,
  );
  expect(await screen.findByText("尚未建立")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "从第一章建立账本" }));
  expect(await screen.findByText("林夏的服装")).toBeInTheDocument();
  expect(screen.getByText("外部请求 0")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("当前状态"), {
    target: { value: "雨夜风衣" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览后续影响" }));
  expect(await screen.findByText(/影响 1 个后续章节、1 个分格/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
  expect(await screen.findByText(/不可变账本版本/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "批准当前状态" }));
  expect(await screen.findByText(/可以继续推进下一章/)).toBeInTheDocument();

  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(writes).toHaveLength(4);
  for (const [, init] of writes) {
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
    {
      chapter_id: "chapter-2",
      version: 1,
      ordinal: 2,
      title: "第二章",
      start_offset: 20,
      end_offset: 40,
      text_sha256: "b".repeat(64),
    },
  ],
};

const impact: ContinuityImpact = {
  changed_entries: [
    {
      stable_key: `outfit:${"a".repeat(24)}`,
      kind: "outfit" as const,
      name: "林夏的服装",
      change: "changed" as const,
    },
  ],
  affected_chapters: [
    { chapter_id: "chapter-2", ordinal: 2, title: "第二章", panel_count: 1 },
  ],
  affected_panel_ids: ["panel-2"],
  requires_future_review: true,
  external_requests_started: 0 as const,
};

function ledgerVersion(
  approvalStatus: "draft" | "approved",
  version: number,
): ContinuityVersion {
  return {
    continuity_ledger_id: "ledger-1",
    continuity_version_id: "continuity-version-1",
    project_id: "project-1",
    version,
    parent_version_id: null,
    through_chapter_id: "chapter-1",
    through_chapter_ordinal: 1,
    through_chapter_title: "第一章",
    source_storyboard_version_id: "storyboard-1",
    source_character_bible_version_id: "character-1",
    document_sha256: "c".repeat(64),
    document: {
      schema_version: "1.0" as const,
      continuity_ledger_id: "ledger-1",
      project_id: "project-1",
      through_chapter_id: "chapter-1",
      through_chapter_ordinal: 1,
      entries: [
        {
          entry_id: "entry-1",
          kind: "outfit" as const,
          stable_key: `outfit:${"a".repeat(24)}`,
          name: "林夏的服装",
          status: "current",
          attributes: { character_name: "林夏", items: "浅色衬衫" },
          notes: "",
          source_chapter_ids: ["chapter-1"],
          source_panel_ids: ["panel-1"],
        },
      ],
      notes: "",
    },
    provenance: {},
    impact: { ...impact, changed_entries: [], affected_chapters: [], affected_panel_ids: [], requires_future_review: false },
    approval_status: approvalStatus,
    approval_hash: null,
    approved_at: null,
    is_current: true,
    created_at: "2026-08-09 00:00:00",
    external_requests_started: 0 as const,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
