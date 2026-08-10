import { useEffect, useState } from "react";

import {
  ApiError,
  type ChapterSet,
  type ExportFile,
  type ExportPreflight,
  type ExportRevision,
  type ImportPreflight,
  createExport,
  downloadExportFile,
  listExports,
  preflightExport,
  preflightProjectPackage,
  restoreProjectPackage,
} from "./api";

interface ExportCenterProps {
  projectId: string;
  chapterSet: ChapterSet;
  onError: (message: string) => void;
  onProjectRestored?: (projectId: string) => void | Promise<void>;
}

export function ExportCenter({
  projectId,
  chapterSet,
  onError,
  onProjectRestored,
}: ExportCenterProps) {
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [plan, setPlan] = useState<ExportPreflight | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [exports, setExports] = useState<ExportRevision[]>([]);
  const [importPlan, setImportPlan] = useState<ImportPreflight | null>(null);
  const [restoreConfirmed, setRestoreConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setPlan(null);
    setConfirmed(false);
    listExports(projectId)
      .then((items) => {
        if (active) setExports(items);
      })
      .catch((error: unknown) => {
        if (active) onError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [chapterId, onError, projectId]);

  async function handlePreflight() {
    await run(async () => {
      const result = await preflightExport(projectId, chapterId);
      setPlan(result);
      setConfirmed(false);
      setMessage("已冻结页面版本、顺序和哈希；尚未写出文件。");
    });
  }

  async function handleExport() {
    if (!plan || !confirmed) return;
    await run(async () => {
      const result = await createExport(projectId, plan);
      setExports((current) => [result, ...current]);
      setPlan(null);
      setConfirmed(false);
      setMessage(
        result.secret_scan?.matches === 0
          ? "四种格式已校验并通过凭证零泄露扫描，现已发布为新的不可变导出。"
          : "工程包、PNG、PDF 和 CBZ 已全部校验并发布为新的不可变导出。",
      );
    });
  }

  async function handleDownload(revision: ExportRevision, file: ExportFile) {
    await run(async () => {
      const blob = await downloadExportFile(
        projectId,
        revision.export_revision_id,
        file.export_file_id,
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setMessage(`已下载 ${file.filename}。`);
    });
  }

  async function handlePackage(file: File | undefined) {
    if (!file) return;
    await run(async () => {
      const result = await preflightProjectPackage(file);
      setImportPlan(result);
      setRestoreConfirmed(false);
      setMessage("工程包 dry-run 通过；尚未创建项目或写入工程文件。");
    });
  }

  async function handleRestore() {
    if (!importPlan || !restoreConfirmed) return;
    await run(async () => {
      const result = await restoreProjectPackage(importPlan.import_preflight_id);
      setMessage(
        result.id_conflict_remapped
          ? `已恢复为新项目“${result.title}”，原项目 ID 冲突已安全重映射。`
          : `已恢复项目“${result.title}”。`,
      );
      setImportPlan(null);
      setRestoreConfirmed(false);
      await onProjectRestored?.(result.project_id);
    });
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

  return (
    <section className="export-center" aria-label="导出与工程包恢复">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第九步</p>
          <h2>导出与恢复</h2>
        </div>
        <span>{exports.filter((item) => item.status === "completed").length} 个成功版本</span>
      </div>
      <p className="panel-description">
        每次导出固定页面版本清单。发布文件不含原文、提示词、凭证或参考图原件；工程包包含可编辑工程，但不包含凭证。
      </p>

      <div className="export-grid">
        <section className="export-card">
          <h3>四种格式导出</h3>
          <label>
            <span>章节</span>
            <select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
              {chapterSet.chapters.map((chapter) => (
                <option key={chapter.chapter_id} value={chapter.chapter_id}>
                  {chapter.ordinal}. {chapter.title}
                </option>
              ))}
            </select>
          </label>
          <button type="button" disabled={busy || !chapterId} onClick={() => void handlePreflight()}>
            预检并冻结页面版本
          </button>
          {plan && (
            <div className="export-plan">
              <strong>{plan.page_count} 页 · 工程包 / PNG / PDF / CBZ</strong>
              <ol>
                {plan.pages.map((page) => (
                  <li key={page.page_version_id}>
                    第 {page.page_number} 页 · v{page.version} · {page.render_sha256.slice(0, 12)}
                  </li>
                ))}
              </ol>
              <label className="consent-row">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                <span>我确认以上页面版本和顺序，并创建新的不可变导出。</span>
              </label>
              <button type="button" disabled={busy || !confirmed} onClick={() => void handleExport()}>
                生成并校验四种格式
              </button>
            </div>
          )}
        </section>

        <section className="export-card">
          <h3>恢复工程包</h3>
          <label className="file-drop compact-drop">
            <strong>选择 .manga-maker.zip</strong>
            <span>先检查路径、大小、压缩比、schema 和 SHA-256，不立即写入</span>
            <input
              type="file"
              accept=".zip,.manga-maker.zip,application/zip"
              disabled={busy}
              onChange={(event) => void handlePackage(event.target.files?.[0])}
            />
          </label>
          {importPlan && (
            <div className="export-plan">
              <strong>{importPlan.source_title}</strong>
              <p>{importPlan.file_count} 个文件 · {importPlan.page_count} 个选定页面</p>
              <label className="consent-row">
                <input
                  type="checkbox"
                  checked={restoreConfirmed}
                  onChange={(event) => setRestoreConfirmed(event.target.checked)}
                />
                <span>我确认在新的空工作区恢复；任何 ID 冲突都不得覆盖现有项目。</span>
              </label>
              <button type="button" disabled={busy || !restoreConfirmed} onClick={() => void handleRestore()}>
                确认恢复为新项目
              </button>
            </div>
          )}
        </section>
      </div>

      {exports.length > 0 && (
        <div className="export-history">
          <h3>导出历史</h3>
          {exports.map((revision) => (
            <article key={revision.export_revision_id}>
              <div>
                <strong>{revision.chapter_title} · {revision.pages.length} 页</strong>
                <span>{revision.status === "completed" ? "已完成" : "失败，旧版本未受影响"}</span>
              </div>
              {revision.files.map((file) => (
                <button
                  key={file.export_file_id}
                  type="button"
                  className="quiet-button"
                  disabled={busy}
                  onClick={() => void handleDownload(revision, file)}
                >
                  下载 {formatLabel(file)}
                </button>
              ))}
            </article>
          ))}
        </div>
      )}
      {message && <p className="success-message">{message}</p>}
    </section>
  );
}

function formatLabel(file: ExportFile): string {
  if (file.kind === "engineering_package") return "工程包";
  if (file.kind === "png") return `PNG 第 ${file.ordinal} 页`;
  return file.kind.toUpperCase();
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "导出或恢复操作失败。";
}
