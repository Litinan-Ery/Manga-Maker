import layoutFixture from "../../../../contracts/fixtures/v0.3/page-layout-draft.json";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PageLayoutDraft } from "../../generated/api/v03Types";
import { LayoutWorkbench } from "./Workbench";
import { createLayoutFixtureClient } from "./client.fixture";

const fixture = structuredClone(layoutFixture) as PageLayoutDraft;
const chapters = [
  { chapter_id: "01900000-0000-7000-8000-000000000501", title: "第一章" },
];

afterEach(cleanup);

describe("LayoutWorkbench", () => {
  it("restores the backend snapshot and shows hierarchy, shot, impact, and zero-cost boundaries", async () => {
    const client = createLayoutFixtureClient(fixture, {
      initialSnapshot: true,
      initialApproval: "active",
    });
    const getStoryboard = vi.spyOn(client, "getApprovedStoryboard");
    const listCurrent = vi.spyOn(client, "listCurrent");
    const getApproval = vi.spyOn(client, "getApproval");
    const getImpact = vi.spyOn(client, "getImpact");

    renderWorkbench(client);

    expect(await screen.findByText("版式版本 1")).toBeInTheDocument();
    expect(screen.getByText("当前版本已批准")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "格框层级与阅读顺序" })).toHaveTextContent(
      "容器 · establishing",
    );
    expect(screen.getByRole("heading", { name: "格 1 属性" })).toBeInTheDocument();
    expect(screen.getByText("Prompt")).toBeInTheDocument();
    expect(screen.getByText(/候选数：0/)).toBeInTheDocument();
    expect(screen.getByText(/图像成本：0/)).toBeInTheDocument();
    expect(getStoryboard).toHaveBeenCalledOnce();
    expect(listCurrent).toHaveBeenCalledOnce();
    expect(getApproval).toHaveBeenCalledOnce();
    expect(getImpact).toHaveBeenCalledOnce();
  });

  it("edits shot scale, moves keyboard reading order, validates dimensions, and approves locally", async () => {
    const client = createLayoutFixtureClient(fixture, { initialSnapshot: true });
    const approve = vi.spyOn(client, "approve");
    renderWorkbench(client);
    await screen.findByText("版式版本 1");

    fireEvent.change(screen.getByLabelText("景别"), { target: { value: "close_up" } });
    expect(screen.getByLabelText("景别")).toHaveValue("close_up");
    fireEvent.keyDown(screen.getByRole("button", { name: /格 1 · close_up/ }), {
      key: "ArrowDown",
      altKey: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    expect(await screen.findByText("版式版本 2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "校验版式与尺寸" }));
    expect(await screen.findByText("1216 × 832")).toBeInTheDocument();
    expect(screen.getAllByText("2.0%")).not.toHaveLength(0);
    fireEvent.click(screen.getByLabelText(/我已核对受影响对象/));
    fireEvent.click(screen.getByRole("button", { name: "批准当前版式" }));

    expect(await screen.findByText("当前版本已批准")).toBeInTheDocument();
    expect(approve).toHaveBeenCalledOnce();
    expect((await approve.mock.results[0].value).external_requests_started).toBe(0);
  });

  it("marks an active approval for invalidation as soon as layout changes", async () => {
    const client = createLayoutFixtureClient(fixture, {
      initialSnapshot: true,
      initialApproval: "active",
    });
    renderWorkbench(client);
    await screen.findByText("当前版本已批准");

    fireEvent.change(screen.getByLabelText("景别"), { target: { value: "wide" } });
    expect(screen.getByText("修改后旧审批将失效")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    expect(await screen.findByText("旧审批已因新版本失效")).toBeInTheDocument();
  });

  it("keeps a local draft on revision conflict and reloads only after explicit action", async () => {
    const client = createLayoutFixtureClient(fixture, {
      initialSnapshot: true,
      saveError: "revision_conflict",
    });
    const listCurrent = vi.spyOn(client, "listCurrent");
    renderWorkbench(client);
    await screen.findByText("版式版本 1");

    fireEvent.change(screen.getByLabelText("景别"), { target: { value: "close_up" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    expect(await screen.findByText(/revision conflict/)).toBeInTheDocument();
    expect(screen.getByLabelText("景别")).toHaveValue("close_up");
    expect(screen.getByRole("button", { name: "保存为新版本" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "重新加载后端当前版本" }));
    await waitFor(() => expect(screen.getByLabelText("景别")).toHaveValue("medium"));
    expect(listCurrent).toHaveBeenCalledTimes(2);
  });
});

function renderWorkbench(client: ReturnType<typeof createLayoutFixtureClient>) {
  return render(
    <LayoutWorkbench
      projectId="01900000-0000-7000-8000-000000000502"
      chapters={chapters}
      client={client}
      onError={(message) => {
        throw new Error(message);
      }}
    />,
  );
}
