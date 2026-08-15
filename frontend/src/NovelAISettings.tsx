import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type NovelAICapabilities,
  type NovelAIConfiguration,
  type VaultStatus,
  getNovelAICapabilities,
  getNovelAIConfiguration,
  saveNovelAIConfiguration,
  testNovelAIConnection,
} from "./api";

interface NovelAISettingsProps {
  projectId: string;
  vaultStatus: VaultStatus | null;
  onError: (message: string) => void;
}

export function NovelAISettings({ projectId, vaultStatus, onError }: NovelAISettingsProps) {
  const [capabilities, setCapabilities] = useState<NovelAICapabilities | null>(null);
  const [configuration, setConfiguration] = useState<NovelAIConfiguration | null>(null);
  const [modelId, setModelId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const novelAIProfiles = useMemo(
    () => vaultStatus?.profiles.filter((profile) => profile.provider === "novelai") ?? [],
    [vaultStatus],
  );

  useEffect(() => {
    let active = true;
    setCapabilities(null);
    setConfiguration(null);
    setMessage("");
    Promise.all([
      getNovelAICapabilities(projectId),
      getNovelAIConfiguration(projectId).catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),
    ])
      .then(([nextCapabilities, nextConfiguration]) => {
        if (!active) return;
        setCapabilities(nextCapabilities);
        setConfiguration(nextConfiguration);
        const recommended = nextCapabilities.models.find((model) => model.recommended);
        setModelId(nextConfiguration?.provider_model_id ?? recommended?.provider_model_id ?? "");
        setProfileId(nextConfiguration?.credential_profile_id ?? novelAIProfiles[0]?.profile_id ?? "");
        setTimeoutSeconds(nextConfiguration?.timeout_seconds ?? 30);
      })
      .catch((error: unknown) => {
        if (active) onError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [projectId, onError]);

  useEffect(() => {
    if (!profileId && novelAIProfiles.length > 0) {
      setProfileId(novelAIProfiles[0].profile_id);
    }
  }, [novelAIProfiles, profileId]);

  async function handleSave() {
    if (!modelId || !profileId) return;
    await run(async () => {
      const saved = await saveNovelAIConfiguration(projectId, {
        provider_model_id: modelId,
        credential_profile_id: profileId,
        timeout_seconds: timeoutSeconds,
      });
      setConfiguration(saved);
      setMessage("NovelAI 配置已保存在本机；尚未调用外部接口。");
    });
  }

  async function handleTest() {
    await run(async () => {
      const result = await testNovelAIConnection(projectId);
      setConfiguration((current) =>
        current
          ? {
              ...current,
              last_connection_status: "ok",
              last_connection_at: result.last_connection_at,
            }
          : current,
      );
      setMessage(
        result.zero_anlas_ready
          ? "连接与订阅核验通过：当前为有效 Opus；仅查询标签与订阅，生成图片 0 张。"
          : !result.model_supports_zero_anlas
            ? "连接可用，但当前模型不支持已冻结的零 Anlas 载荷；请选择 Anime V4.5，或在生成控制台明确使用标准计费。"
            : `连接可用，但订阅层级 ${result.subscription.subscription_tier} 不是有效 Opus；零 Anlas 队列会在出图前停止。`,
      );
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

  const selectedModel = capabilities?.models.find(
    (model) => model.provider_model_id === modelId,
  );
  const canSave = Boolean(modelId && profileId && vaultStatus?.unlocked);

  return (
    <section className="novelai-settings" aria-label="NovelAI 图像接口">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第六步</p>
          <h2>连接 NovelAI</h2>
        </div>
        <span>{configuration?.last_connection_status === "ok" ? "连接已验证" : "尚未验证"}</span>
      </div>
      <p className="panel-description">
        默认使用 Opus 零 Anlas 配置。保存不会联网；连接测试只查询标签与订阅状态，不生成图片、不自动重试。
      </p>

      {!capabilities && <p className="empty-state">正在读取本地 NovelAI 契约…</p>}
      {capabilities && (
        <div className="settings-form">
          <label>
            <span>图像模型</span>
            <select value={modelId} onChange={(event) => setModelId(event.target.value)}>
              {capabilities.models.map((model) => (
                <option key={model.provider_model_id} value={model.provider_model_id}>
                  {model.label}{model.recommended ? "（推荐）" : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>本地 NovelAI 凭证</span>
            <select
              value={profileId}
              onChange={(event) => setProfileId(event.target.value)}
              disabled={novelAIProfiles.length === 0}
            >
              {novelAIProfiles.length === 0 && <option value="">请先在凭证库添加 NovelAI Token</option>}
              {novelAIProfiles.map((profile) => (
                <option key={profile.profile_id} value={profile.profile_id}>
                  {profile.label} · {profile.fingerprint}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>连接超时（秒）</span>
            <input
              type="number"
              min={1}
              max={180}
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
            />
          </label>
          {selectedModel && (
            <p className="field-note">
              {selectedModel.supports_precise_reference
                ? "支持 V4.5 Precise Reference"
                : "不支持 Precise Reference"}
              ；{selectedModel.supports_opus_zero_anlas
                ? "支持已冻结的 Opus 零 Anlas 载荷"
                : !selectedModel.supports_multi_character_prompt
                  ? "当前结构化 V4 生成链路不支持此模型"
                : "仅支持标准计费，零 Anlas 预检会停止"}
              ；{selectedModel.prompt_token_note}
            </p>
          )}
          <div className="zero-anlas-profile" role="note">
            <strong>Opus 零 Anlas 默认上限</strong>
            <span>
              {capabilities.opus_zero_anlas_profile.default_dimensions
                .map((size) => `${size.width}×${size.height}`)
                .join(" / ")} · 单次 1 张 · 最多 {capabilities.opus_zero_anlas_profile.max_steps} 步
            </span>
            <small>不允许基础图、局部重绘或 Precise Reference；执行前会逐张实时核验 Opus。</small>
          </div>
          <div className="button-row">
            <button type="button" disabled={busy || !canSave} onClick={() => void handleSave()}>
              仅保存本地配置
            </button>
            <button
              type="button"
              className="quiet-button"
              disabled={busy || !configuration || configuration.credential_status !== "available"}
              onClick={() => void handleTest()}
            >
              由我触发连接测试（不生成图片）
            </button>
          </div>
          <p className="contract-note">
            契约 {capabilities.mapping_version} · SHA-256 {capabilities.sha256.slice(0, 12)}… ·
            核对日期 {capabilities.fetched_on}
          </p>
        </div>
      )}
      {message && <p className="success-message" role="status">{message}</p>}
    </section>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "NovelAI 设置操作失败。";
}
