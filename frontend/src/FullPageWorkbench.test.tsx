import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FullPageWorkbench } from "./FullPageWorkbench";
import { clearLocalSession, consumeLocalSession, type ChapterSet } from "./api";
import type { FullPageGeneration } from "./fullPages";

afterEach(() => { cleanup(); clearLocalSession(); vi.unstubAllGlobals(); });

it("previews a whole page, invalidates edited approval, generates once and adopts after review", async () => {
  window.history.replaceState(null, "", "/#session=unit-session&csrf=unit-csrf"); consumeLocalSession();
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:full-page"), revokeObjectURL: vi.fn() });
  let calls = 0; let previewCount = 0; let adopted = false;
  const onError = vi.fn(); const onAdopted = vi.fn();
  const plan: FullPageGeneration = {
    generation_id: "full-page-1", page_id: "page-1", page_number: 1, panel_count: 3,
    plan_sha256: "a".repeat(64), status: "draft", generation_calls: 1, verification_calls: 1,
    external_requests_started: 0, cost_ceiling_anlas: 0, removed_conflicting_tags: [], error_code: null,
    created_at: "2026-09-06", options: { chapter_id: "chapter-1", page_number: 1, text_policy: "local", seed: 42, cost_ceiling_anlas: 0, panel_descriptions: {}, panel_texts: {} },
    provider_payload: { input: "manga, comic\n\nPanel 1: opens a door. Panel 2: close-up. Panel 3: reaction.", model: "nai-diffusion-5-full", parameters: {
      width: 832, height: 1216, negative_prompt: "bad anatomy, text", qualityToggle: false, tag_hint_qt: 0, tag_hint_uc_preset: 0,
    } },
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("full-pages/sources?")) return Response.json({ pages: [{ page_id: "page-1", page_number: 1, panels: [1, 2, 3].map((n) => ({ panel_id: `panel-${n}`, visual_description: "She opens a door.", texts: [], characters: ["阿青"] })) }] });
    if (path.includes("full-pages?")) return Response.json([]);
    if (path.endsWith("/preview")) { previewCount++; plan.generation_id = `full-page-${previewCount}`; return Response.json(plan); }
    if (path.endsWith("/generate")) {
      expect(JSON.parse(String(init?.body))).toEqual({ plan_sha256: plan.plan_sha256, confirmed: true });
      expect(new Headers(init?.headers).get("X-Manga-Maker-Session")).toBe("unit-session");
      calls++; return Response.json({ ...plan, status: "ready", external_requests_started: 2 });
    }
    if (path.endsWith("/content")) return new Response(new Blob(["png"], { type: "image/png" }));
    if (path.includes("/pages?")) return Response.json([]);
    if (path.endsWith("/adopt")) { adopted = true; return Response.json({}); }
    throw new Error(`Unexpected ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const chapterSet = { chapters: [{ chapter_id: "chapter-1", title: "雨夜" }] } as ChapterSet;
  render(<FullPageWorkbench projectId="project-1" chapterSet={chapterSet} onError={onError} onAdopted={onAdopted} />);
  const preview = screen.getByRole("button", { name: "预览本页完整 Prompt" });
  await waitFor(() => expect(preview).toBeEnabled());
  fireEvent.click(preview);
  expect(await screen.findByLabelText("整页最终正向 Prompt")).toHaveValue(plan.provider_payload.input);
  const generate = screen.getByRole("button", { name: "确认并生成本页一次" });
  expect(generate).toBeDisabled(); expect(calls).toBe(0);
  fireEvent.click(screen.getByLabelText(/我确认发送以上提示/));
  expect(generate).toBeEnabled();
  fireEvent.click(screen.getByLabelText("分镜 1 出镜：阿青"));
  expect(screen.queryByRole("button", { name: "确认并生成本页一次" })).toBeNull();
  fireEvent.change(screen.getByLabelText("画面描述 1"), { target: { value: "A hand reaches, trembling.\nThe door remains closed." } });
  expect(screen.queryByRole("button", { name: "确认并生成本页一次" })).toBeNull();
  fireEvent.click(preview);
  await screen.findByLabelText(/我确认发送以上提示/);
  const lastPreview = fetchMock.mock.calls.filter(([path]) => String(path).endsWith("/preview")).at(-1);
  expect(JSON.parse(String(lastPreview?.[1]?.body)).panel_characters).toEqual({ "panel-1": [] });
  expect(screen.getByRole("button", { name: "确认并生成本页一次" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/我确认发送以上提示/));
  fireEvent.click(screen.getByRole("button", { name: "确认并生成本页一次" }));
  await screen.findByAltText("待审阅的整页漫画成图");
  expect(calls).toBe(1); expect(previewCount).toBe(2);
  const adopt = screen.getByRole("button", { name: "采用成图为漫画页" });
  expect(adopt).toBeDisabled();
  fireEvent.click(screen.getByLabelText("已检查格数、阅读顺序、人物与文字")); fireEvent.click(adopt);
  await waitFor(() => expect(adopted).toBe(true));
  expect(onAdopted).toHaveBeenCalledOnce(); expect(onError).not.toHaveBeenCalled(); expect(calls).toBe(1);
});

it.each([
  { nextChapter: "chapter-2", delayed: false },
  { nextChapter: "chapter-1", delayed: false },
  { nextChapter: "chapter-2", delayed: true },
])("invalidates old previews when the chapter set changes ($nextChapter, delayed=$delayed)", async ({ nextChapter, delayed }) => {
  window.history.replaceState(null, "", "/#session=unit-session&csrf=unit-csrf"); consumeLocalSession();
  const onError = vi.fn(); const onAdopted = vi.fn();
  const plan: FullPageGeneration = {
    generation_id: "old-plan", page_id: "page-1", page_number: 1, panel_count: 3,
    plan_sha256: "a".repeat(64), status: "draft", generation_calls: 1, verification_calls: 1,
    external_requests_started: 0, cost_ceiling_anlas: 0, removed_conflicting_tags: [], error_code: null,
    created_at: "2026-09-06", options: { chapter_id: "chapter-1", page_number: 1, text_policy: "local", seed: 42, cost_ceiling_anlas: 0, panel_descriptions: {}, panel_texts: {} },
    provider_payload: { input: "Old chapter prompt", model: "nai-diffusion-5-full", parameters: {
      width: 832, height: 1216, negative_prompt: "text", qualityToggle: false, tag_hint_qt: 0, tag_hint_uc_preset: 0,
    } },
  };
  let finishPreview: (value: Response) => void = () => { throw new Error("No preview pending"); };
  let sourceRevision = 1;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/full-pages/sources")) return Response.json({ pages: [{
      page_id: `page-${sourceRevision}`, page_number: 1,
      panels: [{ panel_id: `panel-${sourceRevision}`, visual_description: `Source revision ${sourceRevision}`, texts: [], characters: [] }],
    }] });
    if (url.pathname.endsWith("/full-pages")) return Response.json([]);
    if (url.pathname.endsWith("/preview")) {
      return delayed ? new Promise<Response>((resolve) => { finishPreview = resolve; }) : Response.json(plan);
    }
    throw new Error(`Unexpected ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const chapterSet = {
    source_file_id: "source-1", chapter_set_id: "set-1", chapter_set_version: 1,
    chapters: [{ chapter_id: "chapter-1", title: "旧章" }],
  } as ChapterSet;
  const { rerender } = render(<FullPageWorkbench projectId="project-1" chapterSet={chapterSet} onError={onError} onAdopted={onAdopted} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "预览本页完整 Prompt" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预览本页完整 Prompt" }));
  if (!delayed) {
    fireEvent.click(await screen.findByLabelText(/我确认发送以上提示/));
    expect(screen.getByRole("button", { name: "确认并生成本页一次" })).toBeEnabled();
  }
  sourceRevision = 2;
  rerender(<FullPageWorkbench projectId="project-1" chapterSet={{ ...chapterSet,
    chapter_set_id: "set-2", chapter_set_version: 2,
    chapters: [{ ...chapterSet.chapters[0], chapter_id: nextChapter, title: "新章" }],
  }} onError={onError} onAdopted={onAdopted} />);
  if (delayed) finishPreview(Response.json(plan));
  await waitFor(() => expect(screen.getByLabelText("画面描述 1")).toHaveValue("Source revision 2"));
  expect(screen.getByLabelText("整页章节")).toHaveValue(nextChapter);
  expect(screen.queryByLabelText("整页最终正向 Prompt")).toBeNull();
  expect(screen.queryByRole("button", { name: "确认并生成本页一次" })).toBeNull();
  expect(screen.getByRole("button", { name: "预览本页完整 Prompt" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "刷新分镜与生成记录" }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => String(path).includes("/sources?")).length).toBe(3));
  const sourceCalls = fetchMock.mock.calls.filter(([path]) => String(path).includes("/sources?"));
  expect(new URL(String(sourceCalls.at(-1)?.[0]), "http://localhost").searchParams.get("chapter_id")).toBe(nextChapter);
  expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith("/generate"))).toBe(false);
  expect(onError).not.toHaveBeenCalled();
});
