import { type ChangeEvent, useEffect, useState } from "react";

import {
  ApiError,
  type BibleBundle,
  type CharacterBibleDocument,
  type CharacterProfile,
  type ChapterSet,
  type ReferenceAsset,
  type StoryboardVersion,
  type StyleBibleDocument,
  approveBible,
  attachBibleReference,
  generateBibleBundle,
  getBibleBundle,
  getCurrentStoryboard,
  getReferenceImage,
  reviseCharacterBible,
  reviseStyleBible,
} from "./api";

interface BibleWorkbenchProps {
  projectId: string;
  chapterSet: ChapterSet;
  onError: (message: string) => void;
  refreshKey: number;
}

export function BibleWorkbench({
  projectId,
  chapterSet,
  onError,
  refreshKey,
}: BibleWorkbenchProps) {
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [storyboard, setStoryboard] = useState<StoryboardVersion | null>(null);
  const [bundle, setBundle] = useState<BibleBundle | null>(null);
  const [characterDraft, setCharacterDraft] = useState<CharacterBibleDocument | null>(null);
  const [styleDraft, setStyleDraft] = useState<StyleBibleDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const characterDirty = Boolean(
    bundle &&
      characterDraft &&
      JSON.stringify(characterDraft) !== JSON.stringify(bundle.character_bible.document),
  );
  const styleDirty = Boolean(
    bundle &&
      styleDraft &&
      JSON.stringify(styleDraft) !== JSON.stringify(bundle.style_bible.document),
  );

  useEffect(() => {
    setChapterId(chapterSet.chapters[0]?.chapter_id ?? "");
  }, [chapterSet]);

  useEffect(() => {
    if (!chapterId) return;
    let active = true;
    setStoryboard(null);
    setBundle(null);
    setCharacterDraft(null);
    setStyleDraft(null);
    Promise.all([
      getCurrentStoryboard(projectId, chapterId).catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),
      getBibleBundle(projectId, chapterId).catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),
    ])
      .then(([nextStoryboard, nextBundle]) => {
        if (!active) return;
        setStoryboard(nextStoryboard);
        applyBundle(nextBundle);
      })
      .catch((error: unknown) => {
        if (active) onError(error instanceof Error ? error.message : "无法读取角色与风格设定。");
      });
    return () => {
      active = false;
    };
  }, [chapterId, onError, projectId, refreshKey]);

  function applyBundle(nextBundle: BibleBundle | null) {
    setBundle(nextBundle);
    setCharacterDraft(
      nextBundle ? structuredClone(nextBundle.character_bible.document) : null,
    );
    setStyleDraft(nextBundle ? structuredClone(nextBundle.style_bible.document) : null);
  }

  async function reloadBundle() {
    applyBundle(await getBibleBundle(projectId, chapterId));
  }

  async function run(action: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    setMessage("");
    try {
      await action();
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : "设定操作失败。");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!storyboard) return;
    await run(async () => {
      applyBundle(await generateBibleBundle(projectId, storyboard.storyboard_version_id));
      setMessage("已在本机根据当前分镜草拟角色设定和默认黑白风格，没有调用外部模型。");
    });
  }

  async function saveCharacters() {
    if (!bundle || !characterDraft) return;
    await run(async () => {
      await reviseCharacterBible(
        projectId,
        bundle.character_bible.version_id,
        characterDraft,
      );
      await reloadBundle();
      setMessage("角色修改已保存为不可变新版本，旧审批不会沿用。");
    });
  }

  async function saveStyle() {
    if (!bundle || !styleDraft) return;
    await run(async () => {
      await reviseStyleBible(projectId, bundle.style_bible.version_id, styleDraft);
      await reloadBundle();
      setMessage("风格修改已保存为不可变新版本，旧审批不会沿用。");
    });
  }

  async function approve(kind: "character" | "style") {
    if (!bundle) return;
    const version = kind === "character" ? bundle.character_bible : bundle.style_bible;
    await run(async () => {
      await approveBible(projectId, kind, version.version_id);
      await reloadBundle();
      setMessage(kind === "character" ? "角色设定已批准。" : "风格板已批准。");
    });
  }

  async function uploadReference(
    kind: "character" | "style",
    input: { file: File; sourceNote: string; rightsConfirmed: boolean; characterId?: string },
  ): Promise<boolean> {
    if (!bundle) return false;
    const version = kind === "character" ? bundle.character_bible : bundle.style_bible;
    return run(async () => {
      await attachBibleReference(projectId, kind, version.version_id, input);
      await reloadBundle();
      setMessage("参考图已校验并绑定到新设定版本，当前设定需要重新批准。");
    });
  }

  function updateCharacter(index: number, patch: Partial<CharacterProfile>) {
    setCharacterDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        characters: current.characters.map((character, characterIndex) =>
          characterIndex === index ? { ...character, ...patch } : character,
        ),
      };
    });
  }

  function addCharacter() {
    setCharacterDraft((current) =>
      current
        ? { ...current, characters: [...current.characters, blankCharacter()] }
        : current,
    );
  }

  function removeCharacter(index: number) {
    setCharacterDraft((current) =>
      current
        ? {
            ...current,
            characters: current.characters.filter((_, characterIndex) => characterIndex !== index),
          }
        : current,
    );
  }

  const storyboardReady = storyboard?.approval_status === "approved";
  return (
    <section className="bible-workbench">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第五步</p>
          <h2>角色与画风设定</h2>
        </div>
        <span>{bundle?.generation_readiness.ready ? "设定已就绪" : "等待设定审批"}</span>
      </div>

      <div className="bible-toolbar">
        <label>
          <span>选择章节</span>
          <select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
            {chapterSet.chapters.map((chapter) => (
              <option key={chapter.chapter_id} value={chapter.chapter_id}>
                {chapter.title}
              </option>
            ))}
          </select>
        </label>
        <button type="button" disabled={busy || !storyboardReady} onClick={() => void generate()}>
          {bundle ? "重新草拟设定版本" : "从已审批分镜草拟设定"}
        </button>
      </div>
      {!storyboardReady && (
        <p className="warning-inline">请先完成当前结构化分镜审批，设定草拟不会调用外部模型。</p>
      )}
      {message && <p className="success-message" role="status">{message}</p>}

      {bundle && characterDraft && styleDraft && (
        <div className="bible-editor">
          <div className={`readiness-banner ${bundle.generation_readiness.ready ? "ready" : "blocked"}`}>
            <strong>
              {bundle.generation_readiness.ready
                ? "角色与风格审批已完成"
                : "后续图像生成仍被门禁阻止"}
            </strong>
            {!bundle.generation_readiness.ready && (
              <span>{bundle.generation_readiness.blockers.join(" ")}</span>
            )}
          </div>

          <section className="character-bible-editor">
            <div className="bible-section-heading">
              <div>
                <h3>角色设定表 · 版本 {bundle.character_bible.version}</h3>
                <span>{bibleStatus(bundle.character_bible.approval_status)}</span>
              </div>
              <button type="button" className="quiet-button" onClick={addCharacter}>
                添加角色
              </button>
            </div>
            {bundle.character_bible.approval_status === "stale" && (
              <p className="warning-inline">分镜输入已经变化，请重新草拟设定。</p>
            )}
            {bundle.character_bible.approval_issues.length > 0 && (
              <ul className="approval-issues">
                {bundle.character_bible.approval_issues.map((issue) => <li key={issue}>{issue}</li>)}
              </ul>
            )}
            <div className="character-grid">
              {characterDraft.characters.map((character, index) => (
                <CharacterCard
                  key={character.character_id}
                  character={character}
                  references={bundle.character_bible.reference_assets.filter(
                    (reference) => reference.character_id === character.character_id,
                  )}
                  projectId={projectId}
                  uploadDisabled={
                    busy || characterDirty || bundle.character_bible.approval_status === "stale"
                  }
                  onChange={(patch) => updateCharacter(index, patch)}
                  onRemove={() => removeCharacter(index)}
                  onUpload={(input) =>
                    uploadReference("character", { ...input, characterId: character.character_id })
                  }
                />
              ))}
            </div>
            <label className="notes-field">
              <span>角色设定备注</span>
              <textarea
                rows={3}
                value={characterDraft.notes}
                onChange={(event) =>
                  setCharacterDraft((current) =>
                    current ? { ...current, notes: event.target.value } : current,
                  )
                }
              />
            </label>
            <div className="bible-actions">
              <span>{characterDirty ? "有尚未保存的角色修改" : "角色版本已保存"}</span>
              <div className="button-row">
              <button
                type="button"
                disabled={
                  busy || !characterDirty || bundle.character_bible.approval_status === "stale"
                }
                onClick={() => void saveCharacters()}
              >
                  保存角色新版本
                </button>
                <button
                  type="button"
                  className="approval-button"
                  disabled={
                    busy ||
                    characterDirty ||
                    bundle.character_bible.approval_status !== "draft" ||
                    bundle.character_bible.approval_issues.length > 0
                  }
                  onClick={() => void approve("character")}
                >
                  批准角色设定
                </button>
              </div>
            </div>
          </section>

          <StyleEditor
            projectId={projectId}
            version={bundle.style_bible.version}
            status={bundle.style_bible.approval_status}
            issues={bundle.style_bible.approval_issues}
            document={styleDraft}
            references={bundle.style_bible.reference_assets}
            dirty={styleDirty}
            busy={busy}
            onChange={setStyleDraft}
            onSave={saveStyle}
            onApprove={() => approve("style")}
            onUpload={(input) => uploadReference("style", input)}
          />
        </div>
      )}
    </section>
  );
}

function CharacterCard({
  character,
  references,
  projectId,
  uploadDisabled,
  onChange,
  onRemove,
  onUpload,
}: {
  character: CharacterProfile;
  references: ReferenceAsset[];
  projectId: string;
  uploadDisabled: boolean;
  onChange: (patch: Partial<CharacterProfile>) => void;
  onRemove: () => void;
  onUpload: (input: ReferenceInput) => Promise<boolean>;
}) {
  return (
    <article className="character-card">
      <header>
        <strong>{character.name}</strong>
        <button type="button" className="quiet-button" onClick={onRemove}>移除角色</button>
      </header>
      <div className="character-fields">
        <TextField label="角色名称" value={character.name} onChange={(name) => onChange({ name })} />
        <TextField label="叙事角色" value={character.narrative_role} onChange={(narrative_role) => onChange({ narrative_role })} />
        <TextField label="年龄段" value={character.age_range} onChange={(age_range) => onChange({ age_range })} />
        <TextField label="脸型与五官" value={character.face_shape} onChange={(face_shape) => onChange({ face_shape })} />
        <TextField label="发型" value={character.hair} onChange={(hair) => onChange({ hair })} />
        <TextField label="体型" value={character.body_type} onChange={(body_type) => onChange({ body_type })} />
        <ListField label="服装（每行一项）" value={character.outfit} onChange={(outfit) => onChange({ outfit })} />
        <ListField label="稳定标志（每行一项）" value={character.signature_features} onChange={(signature_features) => onChange({ signature_features })} />
        <ListField label="允许变化（每行一项）" value={character.variable_features} onChange={(variable_features) => onChange({ variable_features })} />
        <ListField label="禁止变化（每行一项）" value={character.forbidden_changes} onChange={(forbidden_changes) => onChange({ forbidden_changes })} />
        <ListField label="道具（每行一项）" value={character.props} onChange={(props) => onChange({ props })} />
        <ListField label="关系（每行一项）" value={character.relationships} onChange={(relationships) => onChange({ relationships })} />
        <ListField label="表情范围（每行一项）" value={character.expression_range} onChange={(expression_range) => onChange({ expression_range })} />
        <TextAreaField label="角色正向提示词" value={character.positive_prompt_fragment} onChange={(positive_prompt_fragment) => onChange({ positive_prompt_fragment })} />
        <TextAreaField label="角色负面提示词" value={character.negative_prompt_fragment} onChange={(negative_prompt_fragment) => onChange({ negative_prompt_fragment })} />
      </div>
      <ReferenceGallery projectId={projectId} references={references} />
      <ReferenceUploader label="上传角色参考图" disabled={uploadDisabled} onUpload={onUpload} />
    </article>
  );
}

function StyleEditor({
  projectId,
  version,
  status,
  issues,
  document,
  references,
  dirty,
  busy,
  onChange,
  onSave,
  onApprove,
  onUpload,
}: {
  projectId: string;
  version: number;
  status: "draft" | "approved" | "stale";
  issues: string[];
  document: StyleBibleDocument;
  references: ReferenceAsset[];
  dirty: boolean;
  busy: boolean;
  onChange: (document: StyleBibleDocument) => void;
  onSave: () => Promise<void>;
  onApprove: () => Promise<void>;
  onUpload: (input: ReferenceInput) => Promise<boolean>;
}) {
  const update = (patch: Partial<StyleBibleDocument>) => onChange({ ...document, ...patch });
  return (
    <section className="style-bible-editor">
      <div className="bible-section-heading">
        <div>
          <h3>黑白漫画风格板 · 版本 {version}</h3>
          <span>{bibleStatus(status)}</span>
        </div>
      </div>
      {status === "stale" && <p className="warning-inline">分镜输入已经变化，请重新草拟设定。</p>}
      {issues.length > 0 && <ul className="approval-issues">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      <div className="style-fields">
        <TextAreaField label="风格摘要" value={document.summary} onChange={(summary) => update({ summary })} />
        <TextAreaField label="线条" value={document.line_art} onChange={(line_art) => update({ line_art })} />
        <TextAreaField label="网点" value={document.screentone} onChange={(screentone) => update({ screentone })} />
        <TextAreaField label="光影" value={document.lighting} onChange={(lighting) => update({ lighting })} />
        <TextAreaField label="背景密度" value={document.background_density} onChange={(background_density) => update({ background_density })} />
        <TextAreaField label="留白" value={document.whitespace} onChange={(whitespace) => update({ whitespace })} />
        <TextAreaField label="镜头语言" value={document.camera_language} onChange={(camera_language) => update({ camera_language })} />
        <TextAreaField label="风格正向提示词" value={document.positive_prompt_fragment} onChange={(positive_prompt_fragment) => update({ positive_prompt_fragment })} />
        <TextAreaField label="风格负面提示词" value={document.negative_prompt_fragment} onChange={(negative_prompt_fragment) => update({ negative_prompt_fragment })} />
        <ListField label="禁止元素（每行一项）" value={document.prohibited_elements} onChange={(prohibited_elements) => update({ prohibited_elements })} />
      </div>
      <ReferenceGallery projectId={projectId} references={references} />
      <ReferenceUploader
        label="上传风格参考图"
        disabled={busy || dirty || status === "stale"}
        onUpload={onUpload}
      />
      <div className="bible-actions">
        <span>{dirty ? "有尚未保存的风格修改" : "风格版本已保存"}</span>
        <div className="button-row">
          <button
            type="button"
            disabled={busy || !dirty || status === "stale"}
            onClick={() => void onSave()}
          >
            保存风格新版本
          </button>
          <button
            type="button"
            className="approval-button"
            disabled={busy || dirty || status !== "draft" || issues.length > 0}
            onClick={() => void onApprove()}
          >
            批准风格板
          </button>
        </div>
      </div>
    </section>
  );
}

interface ReferenceInput {
  file: File;
  sourceNote: string;
  rightsConfirmed: boolean;
}

function ReferenceUploader({
  label,
  disabled,
  onUpload,
}: {
  label: string;
  disabled: boolean;
  onUpload: (input: ReferenceInput) => Promise<boolean>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [sourceNote, setSourceNote] = useState("");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  async function submit() {
    if (!file) return;
    const succeeded = await onUpload({ file, sourceNote, rightsConfirmed });
    if (!succeeded) return;
    setFile(null);
    setSourceNote("");
    setRightsConfirmed(false);
  }
  return (
    <div className="reference-uploader">
      <strong>{label}</strong>
      <input
        aria-label={`${label}文件`}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        disabled={disabled}
        onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] ?? null)}
      />
      <input
        aria-label={`${label}来源说明`}
        value={sourceNote}
        maxLength={500}
        disabled={disabled}
        placeholder="来源说明，例如：本人绘制"
        onChange={(event) => setSourceNote(event.target.value)}
      />
      <label className="rights-confirmation">
        <input
          type="checkbox"
          checked={rightsConfirmed}
          disabled={disabled}
          onChange={(event) => setRightsConfirmed(event.target.checked)}
        />
        <span>我拥有或获准使用这张参考图</span>
      </label>
      <button
        type="button"
        className="quiet-button"
        disabled={disabled || !file || !sourceNote.trim() || !rightsConfirmed}
        onClick={() => void submit()}
      >
        校验并绑定参考图
      </button>
      <small>仅支持 PNG/JPEG/WebP，最大 10 MB；文件内容不会进入数据库。</small>
    </div>
  );
}

function ReferenceGallery({ projectId, references }: { projectId: string; references: ReferenceAsset[] }) {
  if (!references.length) return <p className="empty-reference">尚未绑定参考图。</p>;
  return (
    <div className="reference-gallery">
      {references.map((reference) => (
        <article key={reference.reference_asset_id}>
          <ReferenceImage projectId={projectId} reference={reference} />
          <div>
            <strong>{reference.original_filename}</strong>
            <span>{reference.width} × {reference.height} · {formatBytes(reference.byte_size)}</span>
            <small>{reference.source_note}</small>
            <code>{reference.sha256.slice(0, 12)}</code>
          </div>
        </article>
      ))}
    </div>
  );
}

function ReferenceImage({ projectId, reference }: { projectId: string; reference: ReferenceAsset }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    getReferenceImage(projectId, reference.reference_asset_id)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch(() => setSource(""));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [projectId, reference.reference_asset_id]);
  return source ? <img src={source} alt={reference.original_filename} /> : <div className="reference-placeholder">本地图</div>;
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label><span>{label}</span><input value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

function TextAreaField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label><span>{label}</span><textarea rows={3} value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

function ListField({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
  return <label><span>{label}</span><textarea rows={3} value={value.join("\n")} onChange={(event) => onChange(splitLines(event.target.value))} /></label>;
}

function splitLines(value: string): string[] {
  return [...new Set(value.split("\n").map((item) => item.trim()).filter(Boolean))];
}

function blankCharacter(): CharacterProfile {
  return {
    character_id: crypto.randomUUID(),
    name: "新角色",
    aliases: [],
    narrative_role: "待补充",
    age_range: "待补充",
    face_shape: "待补充",
    hair: "待补充",
    body_type: "待补充",
    outfit: ["待补充"],
    signature_features: ["待补充"],
    variable_features: [],
    forbidden_changes: ["待补充"],
    props: [],
    relationships: [],
    expression_range: ["待补充"],
    positive_prompt_fragment: "待补充",
    negative_prompt_fragment: "inconsistent character design",
    reference_asset_ids: [],
  };
}

function bibleStatus(status: "draft" | "approved" | "stale"): string {
  if (status === "approved") return "已批准";
  if (status === "stale") return "已失效";
  return "待批准";
}

function formatBytes(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}
