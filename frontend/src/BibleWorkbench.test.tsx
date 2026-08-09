import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { BibleWorkbench } from "./BibleWorkbench";
import {
  type BibleBundle,
  type ChapterSet,
  type StoryboardVersion,
  clearLocalSession,
  consumeLocalSession,
} from "./api";

const chapterSet: ChapterSet = {
  source_file_id: "source-1",
  chapter_set_id: "chapter-set-1",
  chapter_set_version: 1,
  chapters: [
    {
      chapter_id: "chapter-1",
      version: 1,
      ordinal: 1,
      title: "第一章 雨夜",
      start_offset: 0,
      end_offset: 30,
      text_sha256: "chapter-hash",
    },
  ],
};

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("independently approves character and style bibles before generation readiness", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  let characterApproved = false;
  let styleApproved = false;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.includes("/storyboards/current?") && method === "GET") {
      return Promise.resolve(jsonResponse(storyboardVersion));
    }
    if (path.includes("/bibles?chapter_id=") && method === "GET") {
      return Promise.resolve(jsonResponse(bibleBundle(characterApproved, styleApproved)));
    }
    if (path.includes("/bibles/character/") && path.endsWith("/approve")) {
      characterApproved = true;
      return Promise.resolve(
        jsonResponse(bibleBundle(true, styleApproved).character_bible),
      );
    }
    if (path.includes("/bibles/style/") && path.endsWith("/approve")) {
      styleApproved = true;
      return Promise.resolve(
        jsonResponse(bibleBundle(characterApproved, true).style_bible),
      );
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  const onError = vi.fn();

  render(
    <BibleWorkbench
      projectId="project-1"
      chapterSet={chapterSet}
      onError={onError}
      refreshKey={0}
    />,
  );

  expect(await screen.findByDisplayValue("林夏")).toBeInTheDocument();
  expect(screen.getByDisplayValue(/black and white manga/)).toBeInTheDocument();
  expect(screen.getByLabelText("上传角色参考图文件")).toBeInTheDocument();
  expect(screen.getByText("后续图像生成仍被门禁阻止")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "批准角色设定" }));
  await waitFor(() => expect(characterApproved).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "批准风格板" }));

  expect(await screen.findByText("角色与风格审批已完成")).toBeInTheDocument();
  expect(onError).not.toHaveBeenCalled();
  const approvalCalls = fetchMock.mock.calls.filter(([path]) =>
    String(path).endsWith("/approve"),
  );
  expect(approvalCalls).toHaveLength(2);
  for (const [, init] of approvalCalls) {
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Manga-Maker-Session")).toBe("session-test");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
  }
});

const storyboardVersion: StoryboardVersion = {
  storyboard_id: "018f0f65-8f2f-7e65-8000-123456789abc",
  storyboard_version_id: "018f0f65-8f2f-7e65-8000-123456789abd",
  version: 1,
  chapter_id: "chapter-1",
  chapter_version: 1,
  beat_set_id: "beat-set-1",
  page_budget: 2,
  source_fingerprint: "f".repeat(64),
  document: {
    schema_version: "1.0",
    storyboard_id: "018f0f65-8f2f-7e65-8000-123456789abc",
    chapter_version: 1,
    beat_resolutions: [],
    scenes: [],
    pages: [],
  },
  provenance: {},
  approval_status: "approved",
  approval_hash: "a".repeat(64),
  approved_at: "2026-08-09T00:00:00Z",
  unresolved_count: 0,
  is_current: true,
  created_at: "2026-08-09T00:00:00Z",
};

function bibleBundle(characterApproved: boolean, styleApproved: boolean): BibleBundle {
  const ready = characterApproved && styleApproved;
  return {
    project_id: "project-1",
    chapter_id: "chapter-1",
    character_bible: {
      kind: "character",
      bible_id: "018f0f65-8f2f-7e65-8000-123456789abe",
      version_id: "018f0f65-8f2f-7e65-8000-123456789abf",
      version: 2,
      storyboard_version_id: storyboardVersion.storyboard_version_id,
      document: {
        schema_version: "1.0",
        character_bible_id: "018f0f65-8f2f-7e65-8000-123456789abe",
        storyboard_version_id: storyboardVersion.storyboard_version_id,
        notes: "角色已补全",
        characters: [
          {
            character_id: "018f0f65-8f2f-7e65-8000-123456789ac0",
            name: "林夏",
            aliases: [],
            narrative_role: "主角",
            age_range: "20-25 岁",
            face_shape: "鹅蛋脸",
            hair: "齐肩黑发",
            body_type: "清瘦",
            outfit: ["浅色衬衫"],
            signature_features: ["眼下小痣"],
            variable_features: [],
            forbidden_changes: ["小痣不得消失"],
            props: [],
            relationships: [],
            expression_range: ["警觉", "坚定"],
            positive_prompt_fragment: "young woman, shoulder-length black hair",
            negative_prompt_fragment: "long hair, missing beauty mark",
            reference_asset_ids: [],
          },
        ],
      },
      provenance: {},
      approval_status: characterApproved ? "approved" : "draft",
      approval_hash: characterApproved ? "c".repeat(64) : null,
      approved_at: characterApproved ? "2026-08-09T00:00:00Z" : null,
      approval_issues: [],
      reference_assets: [],
      is_current: true,
      created_at: "2026-08-09T00:00:00Z",
    },
    style_bible: {
      kind: "style",
      bible_id: "018f0f65-8f2f-7e65-8000-123456789ac1",
      version_id: "018f0f65-8f2f-7e65-8000-123456789ac2",
      version: 1,
      storyboard_version_id: storyboardVersion.storyboard_version_id,
      document: {
        schema_version: "1.0",
        style_bible_id: "018f0f65-8f2f-7e65-8000-123456789ac1",
        storyboard_version_id: storyboardVersion.storyboard_version_id,
        summary: "黑白分页漫画",
        line_art: "清晰墨线",
        screentone: "克制网点",
        lighting: "高对比光影",
        background_density: "关键镜头完整背景",
        whitespace: "情绪格适度留白",
        camera_language: "中景建立动作关系",
        positive_prompt_fragment: "black and white manga, crisp ink line art",
        negative_prompt_fragment: "color, text, watermark",
        prohibited_elements: ["文字", "水印"],
        reference_asset_ids: [],
      },
      provenance: {},
      approval_status: styleApproved ? "approved" : "draft",
      approval_hash: styleApproved ? "s".repeat(64) : null,
      approved_at: styleApproved ? "2026-08-09T00:00:00Z" : null,
      approval_issues: [],
      reference_assets: [],
      is_current: true,
      created_at: "2026-08-09T00:00:00Z",
    },
    generation_readiness: {
      ready,
      blockers: ready ? [] : ["角色或风格尚未批准。"],
      character_bible_version_id: "018f0f65-8f2f-7e65-8000-123456789abf",
      style_bible_version_id: "018f0f65-8f2f-7e65-8000-123456789ac2",
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
