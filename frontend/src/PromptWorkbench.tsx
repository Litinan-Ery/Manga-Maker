import { useEffect, useState } from "react";

import {
  type ChapterSet,
  type CharacterTagBundleDocument,
  type PromptBundleDocument,
  type PromptingWorkflow,
  approveCharacterTags,
  approvePromptBundle,
  generateCharacterTags,
  generatePromptBundle,
  getPromptingWorkflow,
  reviseCharacterTags,
  revisePromptBundle,
} from "./api";

interface PromptWorkbenchProps {
  projectId: string;
  chapterSet: ChapterSet;
  refreshKey: number;
  onError: (message: string) => void;
}

export function PromptWorkbench({
  projectId,
  chapterSet,
  refreshKey,
  onError,
}: PromptWorkbenchProps) {
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [workflow, setWorkflow] = useState<PromptingWorkflow | null>(null);
  const [tagDraft, setTagDraft] = useState<CharacterTagBundleDocument | null>(null);
  const [promptDraft, setPromptDraft] = useState<PromptBundleDocument | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!chapterSet.chapters.some((chapter) => chapter.chapter_id === chapterId)) {
      setChapterId(chapterSet.chapters[0]?.chapter_id ?? "");
    }
  }, [chapterId, chapterSet]);

  useEffect(() => {
    if (!chapterId) return;
    let active = true;
    getPromptingWorkflow(projectId, chapterId)
      .then((next) => {
        if (active) applyWorkflow(next);
      })
      .catch((error: unknown) => {
        if (active) onError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [chapterId, onError, projectId, refreshKey]);

  function applyWorkflow(next: PromptingWorkflow) {
    setWorkflow(next);
    setTagDraft(
      next.character_tags ? structuredClone(next.character_tags.document) : null,
    );
    setPromptDraft(
      next.prompt_bundle ? structuredClone(next.prompt_bundle.document) : null,
    );
  }

  async function reload() {
    applyWorkflow(await getPromptingWorkflow(projectId, chapterId));
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function updateTag(index: number, field: "fixed_tags" | "negative_tags", value: string) {
    setTagDraft((current) =>
      current
        ? {
            ...current,
            tag_sets: current.tag_sets.map((tag, tagIndex) =>
              tagIndex === index ? { ...tag, [field]: parseTags(value) } : tag,
            ),
          }
        : current,
    );
  }

  function updatePrompt(
    index: number,
    field: "base_visual_tags" | "style_tags" | "negative_tags",
    value: string,
  ) {
    setPromptDraft((current) =>
      current
        ? {
            ...current,
            packages: current.packages.map((item, itemIndex) =>
              itemIndex === index ? { ...item, [field]: parseTags(value) } : item,
            ),
          }
        : current,
    );
  }

  const tagsDirty = Boolean(
    workflow?.character_tags &&
      tagDraft &&
      JSON.stringify(workflow.character_tags.document) !== JSON.stringify(tagDraft),
  );
  const promptsDirty = Boolean(
    workflow?.prompt_bundle &&
      promptDraft &&
      JSON.stringify(workflow.prompt_bundle.document) !== JSON.stringify(promptDraft),
  );

  return (
    <section className="prompt-workbench" aria-label="角色固定 tags 与逐格提示词">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第七步</p>
          <h2>角色固定 tags 与 NovelAI PromptPackage</h2>
        </div>
        <span>{workflow?.generation_readiness.ready ? "提示词已冻结" : "等待审批"}</span>
      </div>

      <div className="bible-toolbar">
        <label>
          <span>选择章节</span>
          <select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
            {chapterSet.chapters.map((chapter) => (
              <option key={chapter.chapter_id} value={chapter.chapter_id}>
                {chapter.ordinal}. {chapter.title}
              </option>
            ))}
          </select>
        </label>
        <label className="consent-row">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          <span>我确认把当前已审批的分镜与设定发送到配置的文本模型</span>
        </label>
      </div>

      <div className="button-row">
        <button
          type="button"
          disabled={busy || !confirmed}
          onClick={() =>
            void run(async () => {
              await generateCharacterTags(projectId, chapterId);
              await reload();
              setConfirmed(false);
              setMessage("角色固定 tags 已由文本模型生成，待人工核对与审批。");
            })
          }
        >
          {workflow?.character_tags ? "重新生成角色 tags" : "生成角色固定 tags"}
        </button>
        <button
          type="button"
          disabled={
            busy ||
            !confirmed ||
            workflow?.character_tags?.approval_status !== "approved"
          }
          onClick={() =>
            void run(async () => {
              await generatePromptBundle(projectId, chapterId);
              await reload();
              setConfirmed(false);
              setMessage("逐格 PromptPackage 已生成，固定 tags 由本地编译器注入。");
            })
          }
        >
          {workflow?.prompt_bundle ? "重新生成 PromptPackage" : "生成逐格 PromptPackage"}
        </button>
      </div>

      {workflow && !workflow.generation_readiness.ready && (
        <ul className="blocking-list">
          {workflow.generation_readiness.blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      )}

      {workflow?.character_tags && tagDraft && (
        <div className="bible-section">
          <div className="section-title-row">
            <div>
              <h3>CharacterTagSet · 版本 {workflow.character_tags.version}</h3>
              <p>固定 tags 跨镜头保持不变；姿势、表情、镜头与场景只放入逐格变量。</p>
            </div>
            <span>{approvalLabel(workflow.character_tags.approval_status)}</span>
          </div>
          {tagDraft.tag_sets.map((tag, index) => (
            <article className="character-card" key={tag.tag_set_id}>
              <h4>{tag.character_name}</h4>
              <label>
                <span>固定 tags（逗号或换行分隔）</span>
                <textarea
                  value={tag.fixed_tags.join(", ")}
                  onChange={(event) => updateTag(index, "fixed_tags", event.target.value)}
                />
              </label>
              <label>
                <span>角色负面 tags</span>
                <textarea
                  value={tag.negative_tags.join(", ")}
                  onChange={(event) => updateTag(index, "negative_tags", event.target.value)}
                />
              </label>
              <small>固定 tags 哈希：{tag.fixed_tags_sha256.slice(0, 12)}…</small>
            </article>
          ))}
          <div className="button-row">
            <button
              type="button"
              disabled={busy || !tagsDirty}
              onClick={() =>
                void run(async () => {
                  await reviseCharacterTags(
                    projectId,
                    workflow.character_tags!.version_id,
                    tagDraft,
                  );
                  await reload();
                  setMessage("角色 tags 已保存为新版本，旧 PromptPackage 自动失效。");
                })
              }
            >
              保存 tags 新版本
            </button>
            <button
              type="button"
              disabled={busy || tagsDirty || workflow.character_tags.approval_status !== "draft"}
              onClick={() =>
                void run(async () => {
                  await approveCharacterTags(
                    projectId,
                    workflow.character_tags!.version_id,
                  );
                  await reload();
                  setMessage("角色固定 tags 已审批。后续逐格 prompt 无权改写固定部分。");
                })
              }
            >
              审批角色固定 tags
            </button>
          </div>
        </div>
      )}

      {workflow?.prompt_bundle && promptDraft && (
        <div className="bible-section">
          <div className="section-title-row">
            <div>
              <h3>PromptPackage · 版本 {workflow.prompt_bundle.version}</h3>
              <p>
                模型 {promptDraft.text_model_name} · 配置修订 {promptDraft.text_model_config_revision}
                · NovelAI {promptDraft.provider_model_id}
              </p>
            </div>
            <span>{approvalLabel(workflow.prompt_bundle.approval_status)}</span>
          </div>
          {promptDraft.packages.map((item, index) => (
            <article className="prompt-package-card" key={item.prompt_package_id}>
              <h4>面板 {item.panel_id.slice(0, 8)}</h4>
              <label>
                <span>画面基础 tags</span>
                <textarea
                  value={item.base_visual_tags.join(", ")}
                  onChange={(event) => updatePrompt(index, "base_visual_tags", event.target.value)}
                />
              </label>
              <label>
                <span>风格 tags</span>
                <textarea
                  value={item.style_tags.join(", ")}
                  onChange={(event) => updatePrompt(index, "style_tags", event.target.value)}
                />
              </label>
              <label>
                <span>逐格负面 tags</span>
                <textarea
                  value={item.negative_tags.join(", ")}
                  onChange={(event) => updatePrompt(index, "negative_tags", event.target.value)}
                />
              </label>
              <div className="prompt-preview">
                <strong>最终正向 prompt</strong>
                <code>{item.compiled_prompt}</code>
                <strong>最终负向 prompt</strong>
                <code>{item.compiled_negative_prompt}</code>
              </div>
            </article>
          ))}
          <div className="button-row">
            <button
              type="button"
              disabled={busy || !promptsDirty}
              onClick={() =>
                void run(async () => {
                  await revisePromptBundle(
                    projectId,
                    workflow.prompt_bundle!.version_id,
                    promptDraft,
                  );
                  await reload();
                  setMessage("PromptPackage 已由本地编译器重新合成并保存为新版本。");
                })
              }
            >
              重新编译并保存
            </button>
            <button
              type="button"
              disabled={
                busy ||
                promptsDirty ||
                workflow.prompt_bundle.approval_status !== "draft"
              }
              onClick={() =>
                void run(async () => {
                  await approvePromptBundle(projectId, workflow.prompt_bundle!.version_id);
                  await reload();
                  setMessage("逐格 PromptPackage 已审批，可冻结到生成任务。");
                })
              }
            >
              审批全部 PromptPackage
            </button>
          </div>
        </div>
      )}

      {message && <p className="success-message">{message}</p>}
    </section>
  );
}

function parseTags(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function approvalLabel(status: "draft" | "approved" | "stale"): string {
  if (status === "approved") return "已审批";
  if (status === "stale") return "已失效";
  return "待审批";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "提示词操作失败。";
}
