import { type FormEvent, useState } from "react";

import {
  type VaultStatus,
  createVault,
  lockVault,
  saveCredential,
  unlockVault,
} from "./api";

interface CredentialPanelProps {
  status: VaultStatus | null;
  onStatusChange: (status: VaultStatus) => void;
}

export function CredentialPanel({ status, onStatusChange }: CredentialPanelProps) {
  const [masterPassword, setMasterPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [profileId, setProfileId] = useState("text-model");
  const [label, setLabel] = useState("文本模型");
  const [provider, setProvider] = useState("openai-compatible");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

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

  async function handleSaveCredential(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await run(async () => {
      const saved = await saveCredential(profileId, provider, label, secret);
      const profiles = [
        ...(status?.profiles.filter((profile) => profile.profile_id !== saved.profile_id) ?? []),
        saved,
      ].sort((left, right) => left.profile_id.localeCompare(right.profile_id));
      onStatusChange({ configured: true, unlocked: true, profiles });
      setMessage(`已保存 ${saved.label}，界面只保留指纹 ${saved.fingerprint}。`);
    });
    setSecret("");
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
          <form className="settings-form credential-form" onSubmit={(event) => void handleSaveCredential(event)}>
            <label>
              <span>凭证类型</span>
              <select
                aria-label="凭证类型"
                value={provider}
                onChange={(event) => {
                  const nextProvider = event.target.value;
                  setProvider(nextProvider);
                  if (nextProvider === "novelai") {
                    setProfileId("novelai");
                    setLabel("NovelAI 图像生成");
                  } else {
                    setProfileId("text-model");
                    setLabel("文本模型");
                  }
                }}
              >
                <option value="openai-compatible">文本模型（OpenAI-compatible）</option>
                <option value="novelai">NovelAI 图像生成</option>
              </select>
            </label>
            <label>
              <span>凭证标识</span>
              <input
                value={profileId}
                pattern="[a-z0-9][a-z0-9._-]{0,63}"
                maxLength={64}
                onChange={(event) => setProfileId(event.target.value)}
                required
              />
            </label>
            <label>
              <span>显示名称</span>
              <input
                value={label}
                maxLength={128}
                onChange={(event) => setLabel(event.target.value)}
                required
              />
            </label>
            <label className="secret-field">
              <span>API 密钥</span>
              <input
                type="password"
                autoComplete="off"
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
                required
              />
            </label>
            <button type="submit" disabled={busy || secret.length === 0}>
              加密保存凭证
            </button>
          </form>
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
