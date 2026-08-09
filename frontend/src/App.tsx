import { type FormEvent, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type ChapterSet,
  type HealthResponse,
  type Project,
  type SourcePreflight,
  type VaultStatus,
  confirmSource,
  consumeLocalSession,
  createProject,
  getChapters,
  getHealth,
  getVaultStatus,
  listProjects,
  preflightSource,
} from "./api";
import { ChapterEditor } from "./ChapterEditor";
import { BibleWorkbench } from "./BibleWorkbench";
import { CredentialPanel } from "./CredentialPanel";
import { NovelAISettings } from "./NovelAISettings";
import { StoryBeatPanel } from "./StoryBeatPanel";
import { StoryboardWorkbench } from "./StoryboardWorkbench";
import "./styles.css";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; health: HealthResponse }
  | { kind: "error"; message: string };

export function App() {
  const [hasSession] = useState(consumeLocalSession);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [projectTitle, setProjectTitle] = useState("");
  const [preflight, setPreflight] = useState<SourcePreflight | null>(null);
  const [selectedEncoding, setSelectedEncoding] = useState("");
  const [chapterSet, setChapterSet] = useState<ChapterSet | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [vaultStatus, setVaultStatus] = useState<VaultStatus | null>(null);
  const [adaptationRefreshKey, setAdaptationRefreshKey] = useState(0);
  const [bibleRefreshKey, setBibleRefreshKey] = useState(0);

  const selectedProject = useMemo(
    () => projects.find((project) => project.project_id === selectedProjectId),
    [projects, selectedProjectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => setState({ kind: "ready", health }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState({ kind: "error", message: errorMessage(error) });
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!hasSession) return;
    const controller = new AbortController();
    listProjects(controller.signal)
      .then((items) => {
        setProjects(items);
        if (items.length > 0) setSelectedProjectId(items[0].project_id);
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setActionError(errorMessage(error));
        }
      });
    return () => controller.abort();
  }, [hasSession]);

  useEffect(() => {
    if (!hasSession) return;
    getVaultStatus()
      .then(setVaultStatus)
      .catch((error: unknown) => setActionError(errorMessage(error)));
  }, [hasSession]);

  useEffect(() => {
    if (!selectedProjectId) return;
    setPreflight(null);
    setChapterSet(null);
    const controller = new AbortController();
    getChapters(selectedProjectId, controller.signal)
      .then(setChapterSet)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (error instanceof ApiError && error.status === 404) return;
        setActionError(errorMessage(error));
      });
    return () => controller.abort();
  }, [selectedProjectId]);

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setActionError("");
    try {
      const project = await createProject(projectTitle);
      setProjects((current) => [project, ...current]);
      setSelectedProjectId(project.project_id);
      setProjectTitle("");
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleFile(file: File | undefined) {
    if (!file || !selectedProjectId) return;
    setBusy(true);
    setActionError("");
    setPreflight(null);
    try {
      const result = await preflightSource(selectedProjectId, file);
      setPreflight(result);
      setSelectedEncoding(result.recommended_encoding);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmSource() {
    if (!preflight || !selectedProjectId || !selectedEncoding) return;
    setBusy(true);
    setActionError("");
    try {
      const result = await confirmSource(
        selectedProjectId,
        preflight.preflight_id,
        selectedEncoding,
      );
      setChapterSet(result);
      setPreflight(null);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">LOCAL MANGA WORKSPACE</p>
          <h1>Manga Maker</h1>
          <p className="lede">把获得授权的小说章节，改编成可编辑、可恢复的漫画工程。</p>
        </div>
        <span className="stage">早期开发阶段</span>
      </header>

      <section className="status-card" aria-live="polite">
        <div className={`status-dot status-${state.kind}`} aria-hidden="true" />
        <div>
          <h2>{statusTitle(state)}</h2>
          <p>{statusDescription(state)}</p>
        </div>
      </section>

      {state.kind === "ready" && (
        <section className="facts" aria-label="本地服务信息">
          <Fact label="应用版本" value={state.health.version} />
          <Fact label="本地数据库" value={state.health.database === "ok" ? "正常" : "异常"} />
          <Fact
            label="加密凭证库"
            value={
              vaultStatus?.unlocked
                ? "已解锁"
                : vaultStatus?.configured || state.health.vault_configured
                  ? "已配置"
                  : "尚未配置"
            }
          />
        </section>
      )}

      {state.kind === "ready" && !hasSession && (
        <section className="notice" role="status">
          <strong>请从 Manga Maker 启动器打开此页面</strong>
          <p>当前页面没有本地写入权限。关闭此页后重新运行启动命令即可。</p>
        </section>
      )}

      {state.kind === "ready" && hasSession && (
        <CredentialPanel status={vaultStatus} onStatusChange={setVaultStatus} />
      )}

      {state.kind === "ready" && hasSession && (
        <section className="workspace" aria-label="漫画项目工作区">
          <div className="workspace-heading">
            <div>
              <p className="section-kicker">第一步</p>
              <h2>创建或选择项目</h2>
            </div>
            <span>{projects.length} 个本地项目</span>
          </div>

          <form className="inline-form" onSubmit={handleCreateProject}>
            <label>
              <span>项目名称</span>
              <input
                value={projectTitle}
                onChange={(event) => setProjectTitle(event.target.value)}
                placeholder="例如：雨夜侦探 第一章"
                maxLength={200}
                required
              />
            </label>
            <button type="submit" disabled={busy || projectTitle.trim().length === 0}>
              创建项目
            </button>
          </form>

          {projects.length > 0 && (
            <label className="project-picker">
              <span>当前项目</span>
              <select
                value={selectedProjectId}
                onChange={(event) => setSelectedProjectId(event.target.value)}
              >
                {projects.map((project) => (
                  <option key={project.project_id} value={project.project_id}>
                    {project.title}
                  </option>
                ))}
              </select>
            </label>
          )}

          {selectedProject && (
            <section className="import-panel">
              <div className="workspace-heading compact">
                <div>
                  <p className="section-kicker">第二步</p>
                  <h2>导入 TXT 小说</h2>
                </div>
                <span>{selectedProject.title}</span>
              </div>
              <label className="file-drop">
                <strong>{busy ? "正在处理…" : "选择 TXT 文件"}</strong>
                <span>最大 10 MB；原文件与规范化文本均保存在本机项目中</span>
                <input
                  type="file"
                  accept=".txt,text/plain"
                  disabled={busy}
                  onChange={(event) => void handleFile(event.target.files?.[0])}
                />
              </label>
            </section>
          )}

          {preflight && (
            <section className="encoding-panel">
              <div>
                <p className="section-kicker">编码确认</p>
                <h3>{preflight.filename}</h3>
                <p>
                  检测到 {preflight.candidates.length} 个可用编码候选。请核对下方预览，确认后才会写入项目。
                </p>
              </div>
              <label>
                <span>文本编码</span>
                <select
                  value={selectedEncoding}
                  onChange={(event) => setSelectedEncoding(event.target.value)}
                >
                  {preflight.candidates.map((candidate) => (
                    <option key={candidate.encoding} value={candidate.encoding}>
                      {candidate.encoding} · 可信度 {Math.round(candidate.confidence * 100)}%
                    </option>
                  ))}
                </select>
              </label>
              <pre>{preflight.candidates.find((item) => item.encoding === selectedEncoding)?.preview}</pre>
              {preflight.requires_confirmation && (
                <p className="warning">检测结果不够确定，请务必核对中文是否完整、没有乱码。</p>
              )}
              <button type="button" disabled={busy} onClick={() => void handleConfirmSource()}>
                确认编码并识别章节
              </button>
            </section>
          )}

          {chapterSet && (
            <section className="chapters">
              <div className="workspace-heading compact">
                <div>
                  <p className="section-kicker">识别结果</p>
                  <h2>{chapterSet.chapters.length} 个章节</h2>
                </div>
                <span>章节版本 {chapterSet.chapter_set_version}</span>
              </div>
              <ChapterEditor
                projectId={selectedProjectId}
                chapterSet={chapterSet}
                onSaved={setChapterSet}
                onError={setActionError}
              />
              <StoryBeatPanel
                projectId={selectedProjectId}
                chapterSet={chapterSet}
                onError={setActionError}
                onChanged={() => setAdaptationRefreshKey((current) => current + 1)}
              />
              <StoryboardWorkbench
                projectId={selectedProjectId}
                chapterSet={chapterSet}
                vaultStatus={vaultStatus}
                onError={setActionError}
                refreshKey={adaptationRefreshKey}
                onChanged={() => setBibleRefreshKey((current) => current + 1)}
              />
              <BibleWorkbench
                projectId={selectedProjectId}
                chapterSet={chapterSet}
                onError={setActionError}
                refreshKey={bibleRefreshKey}
              />
              <NovelAISettings
                projectId={selectedProjectId}
                vaultStatus={vaultStatus}
                onError={setActionError}
              />
            </section>
          )}

          {actionError && (
            <p className="action-error" role="alert">
              {actionError}
            </p>
          )}
        </section>
      )}
    </main>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "发生未知错误。";
}

function statusTitle(state: LoadState): string {
  if (state.kind === "loading") return "正在连接本地服务";
  if (state.kind === "error") return "本地服务未连接";
  return state.health.status === "ok" ? "后端连接正常" : "本地服务需要检查";
}

function statusDescription(state: LoadState): string {
  if (state.kind === "loading") return "正在确认数据库和本地凭证库状态。";
  if (state.kind === "error") return state.message;
  return state.health.status === "ok"
    ? "所有数据仍保存在这台 Mac 上。"
    : "部分本地组件未通过健康检查。";
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
