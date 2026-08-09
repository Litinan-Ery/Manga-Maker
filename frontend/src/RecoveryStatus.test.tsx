import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { RecoveryStatus } from "./RecoveryStatus";
import { clearLocalSession, consumeLocalSession } from "./api";

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("shows restart findings and runs only an explicitly requested local recheck", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      jsonResponse({
        trigger: "startup",
        status: "needs_attention",
        queue_recovery: { needs_review: 1, paused: 0 },
        integrity: { critical_findings: 0, staging_items: 1 },
        external_requests_started: 0,
      }),
    )
    .mockResolvedValueOnce(
      jsonResponse({
        trigger: "manual",
        status: "healthy",
        queue_recovery: { needs_review: 0, paused: 0 },
        integrity: { critical_findings: 0, staging_items: 0 },
        external_requests_started: 0,
      }),
    );
  vi.stubGlobal("fetch", fetchMock);

  render(<RecoveryStatus onError={vi.fn()} />);
  expect(await screen.findByText("有项目需要人工检查")).toBeInTheDocument();
  expect(screen.getByText(/付费任务没有自动重放/)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
  expect(await screen.findByText("本地工程状态正常")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const headers = new Headers(fetchMock.mock.calls[1][1]?.headers);
  expect(fetchMock.mock.calls[1][1]?.method).toBe("POST");
  expect(headers.get("X-Manga-Maker-Session")).toBe("session-test");
  expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
});

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
