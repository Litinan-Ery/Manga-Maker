import { type FormEvent, useEffect, useRef, useState } from "react";

import {
  ApiError,
  type TextModelConfiguration,
  type VaultStatus,
  createVault,
  getTextModelConfiguration,
  lockVault,
  saveCredential,
  saveTextModelConfiguration,
  unlockVault,
} from "./api";

interface CredentialPanelProps {
  status: VaultStatus | null;
  onStatusChange: (status: VaultStatus) => void;
  projectId?: string | null;
  onTextModelSaved?: () => void;
}

const DEFAULT_TEXT_MODEL_URL = "https://api.openai.com/v1";

export function CredentialPanel({
  status,
  onStatusChange,
  projectId = null,
  onTextModelSaved,
}: CredentialPanelProps) {
  const [masterPassword, setMasterPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [textModelConfiguration, setTextModelConfiguration] =
    useState<TextModelConfiguration | null>(null);
  const [remarkName, setRemarkName] = useState("");
  const [url, setUrl] = useState(DEFAULT_TEXT_MODEL_URL);
  const [keyPassword, setKeyPassword] = useState("");
  const [requestModel, setRequestModel] = useState("");
  const [novelAISecret, setNovelAISecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [textModelLoading, setTextModelLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const activeProjectId = useRef(projectId);
  const requiresKeyPassword =
    !textModelConfiguration || textModelConfiguration.credential_status === "missing";

  useEffect(() => {
    let active = true;
    const projectChanged = activeProjectId.current !== projectId;
    activeProjectId.current = projectId;
    setTextModelConfiguration(null);
    setRemarkName("");
    setUrl(DEFAULT_TEXT_MODEL_URL);
    setKeyPassword("");
    setRequestModel("");
    if (projectChanged) {
      setMessage("");
      setError("");
    }
    if (!status?.unlocked || !projectId) {
      setTextModelLoading(false);
      return () => {
        active = false;
      };
    }

    setTextModelLoading(true);
    getTextModelConfiguration(projectId)
      .then((configuration) => {
        if (!active) return;
        applyTextModelConfiguration(configuration);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 404) {
          setTextModelConfiguration(null);
          setRemarkName("");
          setUrl(DEFAULT_TEXT_MODEL_URL);
          setRequestModel("");
          return;
        }
        setError(caught instanceof Error ? caught.message : "无法读取文本模型配置。");
      })
      .finally(() => {
        if (active) setTextModelLoading(false);
      });

    return () => {
      active = false;
    };
  }, [projectId, status?.unlocked]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (masterPassword !== confirmPassword) {
      setError("两次输入的主密码不一致。");
      return;
    }
    await run(async () => {
      onStatusChange(await createVault(masterPassword));
      setMessage("本地加密凭证库已创建并解锁。");
    });
    clearPasswords();
  }

  async function handleUnlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await run(async () => {
      onStatusChange(await unlockVault(masterPassword));
      setMessage("凭证库已解锁，本次应用关闭时会自动锁定。");
    });
    clearPasswords();
  }

  async function handleSaveTextModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectId) {
      setError("请先创建或选择项目，再配置文本大模型。");
      return;
    }
    if (requiresKeyPassword && !keyPassword) {
      setError("首次配置请输入 Key/Password。保存后不会回显。");
      return;
    }
    await run(async () => {
      const targetProjectId = projectId;
      const saved = await saveTextModelConfiguration(targetProjectId, {
        remark_name: remarkName.trim() || null,
        url,
        request_model: requestModel,
        ...(keyPassword ? { key_password: keyPassword } : {}),
      });
      if (activeProjectId.current !== targetProjectId) return;
      applyTextModelConfiguration(saved);
      setKeyPassword("");
      if (saved.credential_fingerprint) {
        const profile = {
          profile_id: saved.credential_profile_id,
          provider: saved.provider,
          label: saved.remark_name || "文本模型",
          fingerprint: saved.credential_fingerprint,
        };
        const profiles = [
          ...(status?.profiles.filter((item) => item.profile_id !== profile.profile_id) ?? []),
          profile,
        ].sort((left, right) => left.profile_id.localeCompare(right.profile_id));
        onStatusChange({ configured: true, unlocked: true, profiles });
      }
      setMessage("文本大模型配置已加密保存在本机，尚未发出网络请求。");
      onTextModelSaved?.();
    });
  }

  async function handleSaveNovelAI(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await run(async () => {
      const saved = await saveCredential(
        "novelai",
        "novelai",
        "NovelAI 图像生成",
        novelAISecret,
      );
      const profiles = [
        ...(status?.profiles.filter((profile) => profile.profile_id !== saved.profile_id) ?? []),
        saved,
      ].sort((left, right) => left.profile_id.localeCompare(right.profile_id));
      onStatusChange({ configured: true, unlocked: true, profiles });
      setMessage(`已保存 ${saved.label}，界面只保留指纹 ${saved.fingerprint}。`);
    });
    setNovelAISecret("");
  }

  async function handleLock() {
    await run(async () => {
      onStatusChange(await lockVault());
      setMessage("凭证库已锁定。");
    });
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "凭证库操作失败。");
    } finally {
      setBusy(false);
    }
  }

  function clearPasswords() {
    setMasterPassword("");
    setConfirmPassword("");
  }

  function applyTextModelConfiguration(configuration: TextModelConfiguration) {
    setTextModelConfiguration(configuration);
    setRemarkName(configuration.remark_name ?? "");
    setUrl(configuration.url);
    setRequestModel(configuration.request_model);
  }

  return (
    <section className="credential-panel" aria-label="本地凭证库">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">本地安全设置</p>
          <h2>模型凭证库</h2>
        </div>
        <span>{status?.unlocked ? "已解锁" : status?.configured ? "已锁定" : "未创建"}</span>
      </div>
      <p className="panel-description">
        API 密钥只加密保存在 Manga Maker 应用数据目录，不写入项目、SQLite、日志或浏览器存储。
      </p>

      {!status && <p className="empty-state">正在读取本地凭证库状态…</p>}

      {status && !status.configured && (
        <form className="settings-form" onSubmit={(event) => void handleCreate(event)}>
          <label>
            <span>设置主密码（至少 10 个字符）</span>
            <input
              type="password"
              autoComplete="new-password"
              minLength={10}
              value={masterPassword}
              onChange={(event) => setMasterPassword(event.target.value)}
              required
            />
          </label>
          <label>
            <span>再次输入主密码</span>
            <input
              type="password"
              autoComplete="new-password"
              minLength={10}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={busy}>
            创建本地凭证库
          </button>
        </form>
      )}

      {status?.configured && !status.unlocked && (
        <form className="settings-form compact-form" onSubmit={(event) => void handleUnlock(event)}>
          <label>
            <span>主密码</span>
            <input
              type="password"
              autoComplete="current-password"
              minLength={10}
              value={masterPassword}
              onChange={(event) => setMasterPassword(event.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={busy}>
            解锁凭证库
          </button>
        </form>
      )}

      {status?.unlocked && (
        <>
          {status.profiles.length > 0 && (
            <ul className="credential-list">
              {status.profiles.map((profile) => (
                <li key={profile.profile_id}>
                  <div>
                    <strong>{profile.label}</strong>
                    <span>{profile.profile_id}</span>
                  </div>
                  <code>{profile.fingerprint}</code>
                </li>
              ))}
            </ul>
          )}
          <div className="generation-setup">
            <div>
              <p className="section-kicker">当前项目</p>
              <h3>文本大模型配置</h3>
              <p className="panel-description">
                URL、Request Model 与备注保存到当前项目；Key/Password 只进入本地加密凭证库。
              </p>
            </div>
            {!projectId && (
              <p className="warning-inline" role="status">
                请先创建或选择项目，再保存文本大模型配置。
              </p>
            )}
            {textModelLoading && (
              <p className="empty-state" role="status">
                正在读取当前项目的文本模型配置…
              </p>
            )}
            <form
              className="settings-form credential-form"
              onSubmit={(event) => void handleSaveTextModel(event)}
            >
              <label>
                <span>备注名称（可选）</span>
                <input
                  value={remarkName}
                  maxLength={200}
                  placeholder="例如：主力分镜模型"
                  disabled={!projectId || textModelLoading}
                  onChange={(event) => setRemarkName(event.target.value)}
                />
              </label>
              <label>
                <span>URL</span>
                <input
                  type="url"
                  value={url}
                  placeholder="https://api.example.com/v1"
                  disabled={!projectId || textModelLoading}
                  onChange={(event) => setUrl(event.target.value)}
                  required
                />
              </label>
              <label className="secret-field">
                <span>Key/Password</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={keyPassword}
                  placeholder={
                    textModelConfiguration?.credential_status === "missing"
                      ? "凭证缺失，请重新输入"
                      : textModelConfiguration
                        ? "留空则保留当前 Key/Password"
                        : "仅加密保存在本机"
                  }
                  disabled={!projectId || textModelLoading}
                  onChange={(event) => setKeyPassword(event.target.value)}
                  required={requiresKeyPassword}
                />
              </label>
              <label>
                <span>Request Model</span>
                <input
                  value={requestModel}
                  placeholder="例如：gpt-4.1-mini"
                  disabled={!projectId || textModelLoading}
                  onChange={(event) => setRequestModel(event.target.value)}
                  required
                />
              </label>
              <button
                type="submit"
                disabled={
                  busy ||
                  textModelLoading ||
                  !projectId ||
                  !url ||
                  !requestModel ||
                  (requiresKeyPassword && !keyPassword)
                }
              >
                保存文本大模型配置
              </button>
            </form>
            {textModelConfiguration && (
              <p className="configuration-summary">
                当前：
                {textModelConfiguration.remark_name
                  ? `${textModelConfiguration.remark_name} · `
                  : ""}
                {textModelConfiguration.endpoint_host} · {textModelConfiguration.request_model} ·
                Key/Password {textModelConfiguration.credential_fingerprint ?? "已保存"} · 配置版本
                {textModelConfiguration.revision}
              </p>
            )}
          </div>

          <div className="generation-setup">
            <div>
              <p className="section-kicker">图像模型</p>
              <h3>NovelAI 凭证</h3>
            </div>
            <form
              className="settings-form compact-form"
              onSubmit={(event) => void handleSaveNovelAI(event)}
            >
              <label className="secret-field">
                <span>NovelAI Token</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={novelAISecret}
                  onChange={(event) => setNovelAISecret(event.target.value)}
                  required
                />
              </label>
              <button type="submit" disabled={busy || novelAISecret.length === 0}>
                加密保存 NovelAI 凭证
              </button>
            </form>
          </div>
          <button type="button" className="quiet-button lock-button" disabled={busy} onClick={() => void handleLock()}>
            立即锁定
          </button>
        </>
      )}

      {message && <p className="success-message" role="status">{message}</p>}
      {error && <p className="action-error" role="alert">{error}</p>}
    </section>
  );
}
