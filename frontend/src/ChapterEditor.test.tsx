import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { type ChapterSet, clearLocalSession, consumeLocalSession } from "./api";
import { ChapterEditor } from "./ChapterEditor";

const chapterSet: ChapterSet = {
  source_file_id: "source-1",
  chapter_set_id: "set-1",
  chapter_set_version: 1,
  chapters: [
    {
      chapter_id: "chapter-1",
      version: 1,
      ordinal: 1,
      title: "第一章",
      start_offset: 0,
      end_offset: 20,
      text_sha256: "hash",
    },
  ],
};

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("saves a renamed chapter as a new chapter set", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const saved = {
    ...chapterSet,
    chapter_set_id: "set-2",
    chapter_set_version: 2,
    chapters: [{ ...chapterSet.chapters[0], title: "第一章 雨夜" }],
  };
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(saved), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const onSaved = vi.fn();

  render(
    <ChapterEditor
      projectId="project-1"
      chapterSet={chapterSet}
      onSaved={onSaved}
      onError={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("第 1 章标题"), {
    target: { value: "第一章 雨夜" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存章节调整" }));

  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
  const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(path).toBe("/api/v1/projects/project-1/source/chapters");
  expect(init.method).toBe("PUT");
  expect(JSON.parse(String(init.body))).toMatchObject({
    source_file_id: "source-1",
    chapters: [{ title: "第一章 雨夜", start_offset: 0, end_offset: 20 }],
  });
});
