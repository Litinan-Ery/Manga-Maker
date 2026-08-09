import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { PageComposer } from "./PageComposer";
import { clearLocalSession, consumeLocalSession } from "./api";

afterEach(() => {
  cleanup();
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("edits and rerenders a page locally without starting an image request", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:page-preview"),
    revokeObjectURL: vi.fn(),
  });

  const fetchMock = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    const url = String(path);
    const method = init?.method ?? "GET";
    if (url.endsWith("/pages/templates") && method === "GET") {
      return Promise.resolve(jsonResponse([pageTemplate]));
    }
    if (url.includes("/pages?chapter_id=chapter-1") && method === "GET") {
      return Promise.resolve(jsonResponse([]));
    }
    if (url.endsWith("/pages/draft") && method === "POST") {
      return Promise.resolve(jsonResponse([pageVersion(1, "雨还在下。")], 201));
    }
    if (url.endsWith("/versions/page-version-1/content") && method === "GET") {
      return Promise.resolve(new Response(new Blob(["png"], { type: "image/png" })));
    }
    if (url.endsWith("/pages/page-1/versions") && method === "POST") {
      if (!init) throw new Error("missing page revision request");
      const body = JSON.parse(String(init.body)) as {
        document: ReturnType<typeof pageVersion>["document"];
      };
      return Promise.resolve(
        jsonResponse({
          ...pageVersion(2, body.document.text_layers[0].text),
          document: body.document,
          page_revision: 2,
          page_version_id: "page-version-2",
          parent_page_version_id: "page-version-1",
          render_sha256: "e".repeat(64),
        }),
      );
    }
    if (url.endsWith("/versions/page-version-2/content") && method === "GET") {
      return Promise.resolve(new Response(new Blob(["png-v2"], { type: "image/png" })));
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <PageComposer
      projectId="project-1"
      chapterSet={chapterSet}
      onError={vi.fn()}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "从当前素材建立漫画页" }));
  expect(await screen.findByText(/未调用任何图像 API/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("文字"), {
    target: { value: "雨停了，街灯还亮着。" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存并重新渲染页面（仅本地）" }));

  expect(await screen.findByText(/没有产生 NovelAI 请求/)).toBeInTheDocument();
  const saveCall = fetchMock.mock.calls.find(
    ([path, init]) =>
      String(path).endsWith("/pages/page-1/versions") && init?.method === "POST",
  );
  expect(saveCall).toBeDefined();
  const payload = JSON.parse(String(saveCall?.[1]?.body));
  expect(payload.expected_revision).toBe(1);
  expect(payload.document.text_layers[0].text).toBe("雨停了，街灯还亮着。");
  expect(
    fetchMock.mock.calls.some(([path]) =>
      /\/generation\/jobs|\/novelai|\/execute/.test(String(path)),
    ),
  ).toBe(false);
  await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(2));
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

const pageTemplate = {
  template_id: "grid-1",
  label: "单格",
  panel_count: 1,
  frames: [{ x: 96, y: 96, width: 1856, height: 2748 }],
};

function pageVersion(version: number, text: string) {
  return {
    page_id: "page-1",
    project_id: "project-1",
    chapter_id: "chapter-1",
    page_number: 1,
    page_revision: version,
    page_version_id: `page-version-${version}`,
    version,
    parent_page_version_id: version === 1 ? null : `page-version-${version - 1}`,
    storyboard_version_id: "storyboard-version-1",
    document_sha256: "d".repeat(64),
    render_sha256: "r".repeat(64),
    renderer_version: "pillow-page-renderer-v1",
    font_sha256: "f".repeat(64),
    is_current: true,
    created_at: "2026-08-09 12:00:00",
    external_requests_started: 0 as const,
    document: {
      schema_version: "1.0" as const,
      page_id: "page-1",
      page_number: 1,
      width: 2048 as const,
      height: 3072 as const,
      reading_direction: "left_to_right" as const,
      language: "zh-Hans" as const,
      template_id: "grid-1",
      storyboard_version_id: "storyboard-version-1",
      panels: [
        {
          panel_id: "panel-1",
          asset_version_id: "asset-version-1",
          frame: { x: 96, y: 96, width: 1856, height: 2748 },
          focal_x: 0.5,
          focal_y: 0.5,
          zoom: 1,
        },
      ],
      text_layers: [
        {
          layer_id: "layer-1",
          panel_id: "panel-1",
          kind: "narration" as const,
          text,
          speaker: null,
          bounds: { x: 150, y: 140, width: 660, height: 220 },
          font_size: 42,
          align: "left" as const,
        },
      ],
      show_page_number: true,
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
