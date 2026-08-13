import type { PromptInspector, PromptInspectorCharacter } from "./client";

export type CharacterDraftPatch = Partial<
  Pick<PromptInspectorCharacter, "variable_positive_tags" | "negative_tags" | "action">
>;

export interface PromptInspectorViewProps {
  inspector: PromptInspector;
  draftDirty: boolean;
  onCharacterChange: (
    panelId: string,
    characterId: string,
    patch: CharacterDraftPatch,
  ) => void;
  onRelationshipChange: (panelId: string, relationshipAction: string | null) => void;
}

export function PromptInspectorView({
  inspector,
  draftDirty,
  onCharacterChange,
  onRelationshipChange,
}: PromptInspectorViewProps) {
  return (
    <section className="prompt-inspector" aria-label="Prompt Inspector">
      <div className="section-title-row">
        <div>
          <h3>Prompt Inspector · 实际供应商映射</h3>
          <p>仅展示 allowlist 字段；不含 Token、请求头、完整章节或图片 base64。</p>
        </div>
        <span>
          {draftDirty
            ? "本地修改待重编译"
            : inspector.impact.requires_reestimate
              ? "修改后需重新估算"
              : "当前快照"}
        </span>
      </div>

      <div className="inspector-generation-summary" aria-label="候选与成本摘要">
        <strong>候选与成本边界</strong>
        <span>
          {inspector.generation_summary.panel_count} 格 · 每格候选 {" "}
          {inspector.generation_summary.candidate_count_per_panel ?? "需重新估算"} · 预计调用 {" "}
          {inspector.generation_summary.estimated_calls} 次
        </span>
        <span>保守成本上限：需进入生成预估后确认</span>
        <small>{inspector.generation_summary.cost_notice}</small>
      </div>

      {inspector.panels.map((panel) => (
        <details key={panel.prompt_package_id} open>
          <summary>
            面板 {panel.panel_id.slice(0, 8)} · {panel.model_id}
          </summary>
          <div className="inspector-hash-grid">
            <HashFact label="PromptPlan" value={panel.prompt_plan_sha256} pending={draftDirty} />
            <HashFact
              label="ProviderSpec"
              value={panel.provider_execution_spec_sha256}
              pending={draftDirty}
            />
            <HashFact label="Payload" value={panel.provider_payload_sha256} pending={draftDirty} />
          </div>
          <div className="inspector-base-grid">
            <InspectorTags
              title="Base 正向"
              tags={panel.provider_execution_spec.base_positive_tags}
            />
            <InspectorTags
              title="Base 负向"
              tags={panel.provider_execution_spec.base_negative_tags}
            />
          </div>
          <div className="inspector-character-list">
            {panel.prompt_plan.characters.map((character, characterIndex) => {
              const mapped = panel.provider_execution_spec.character_captions[characterIndex];
              const valid =
                !draftDirty &&
                mapped?.character_id === character.character_id &&
                mapped.order === character.order &&
                mapped.center.x === character.center.x &&
                mapped.center.y === character.center.y &&
                mapped.positive_tags.length > 0 &&
                mapped.negative_tags.length > 0;
              return (
                <article
                  key={character.character_id}
                  className={valid ? "mapping-valid" : "mapping-invalid"}
                >
                  <header>
                    <strong>角色 {character.order + 1}</strong>
                    <span>
                      {draftDirty
                        ? "本地修改待保存，禁止审批"
                        : valid
                          ? "领域 ↔ 载荷一致"
                          : "缺失或错位，禁止审批"}
                    </span>
                  </header>
                  <p>
                    顺序 {character.order} · center {character.center.x.toFixed(2)}, {" "}
                    {character.center.y.toFixed(2)}
                  </p>
                  <InspectorTags title="固定 Tags（只读）" tags={character.fixed_tags} />
                  <label>
                    <span>逐格变量 Tags</span>
                    <textarea
                      aria-label={`角色 ${character.order + 1} 逐格变量 Tags`}
                      value={character.variable_positive_tags.join(", ")}
                      onChange={(event) =>
                        onCharacterChange(panel.panel_id, character.character_id, {
                          variable_positive_tags: parseTags(event.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    <span>角色负向 Tags</span>
                    <textarea
                      aria-label={`角色 ${character.order + 1} 负向 Tags`}
                      value={character.negative_tags.join(", ")}
                      onChange={(event) =>
                        onCharacterChange(panel.panel_id, character.character_id, {
                          negative_tags: parseTags(event.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    <span>角色动作</span>
                    <input
                      aria-label={`角色 ${character.order + 1} 动作`}
                      value={character.action}
                      onChange={(event) =>
                        onCharacterChange(panel.panel_id, character.character_id, {
                          action: event.target.value,
                        })
                      }
                    />
                  </label>
                </article>
              );
            })}
          </div>
          <div className="inspector-layout-summary">
            <label>
              <span>关系动作</span>
              <input
                aria-label={`面板 ${panel.panel_id.slice(0, 8)} 关系动作`}
                value={panel.prompt_plan.base.relationship_action ?? ""}
                onChange={(event) =>
                  onRelationshipChange(panel.panel_id, event.target.value.trim() || null)
                }
              />
            </label>
            <span>
              Layout：{String(panel.prompt_plan.layout_constraints.page_layout_draft_id)} · frame {" "}
              {String(panel.prompt_plan.layout_constraints.frame_id)}
            </span>
          </div>
          <details>
            <summary>查看脱敏 NovelAI payload</summary>
            <pre>{JSON.stringify(panel.provider_payload, null, 2)}</pre>
          </details>
        </details>
      ))}

      {inspector.impact.impacts.length > 0 && (
        <ul className="blocking-list">
          {inspector.impact.impacts.map((impact) => (
            <li
              key={`${impact.artifact.artifact_type}:${impact.artifact.artifact_id}:${impact.artifact.version}`}
            >
              修改将影响 {impact.artifact.artifact_type} v{impact.artifact.version}；路径：
              {impact.path
                .map((step) => step.via_edge_type)
                .filter((value): value is string => Boolean(value))
                .join(" → ") || "直接依赖"}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function HashFact({
  label,
  value,
  pending,
}: {
  label: string;
  value: string;
  pending: boolean;
}) {
  return (
    <span>
      <strong>{label}</strong>
      <code>{pending ? "保存后重新计算" : `${value.slice(0, 16)}…`}</code>
    </span>
  );
}

function InspectorTags({ title, tags }: { title: string; tags: string[] }) {
  return (
    <div className="inspector-tags">
      <strong>{title}</strong>
      <span>{tags.length > 0 ? tags.join(", ") : "缺失"}</span>
    </div>
  );
}

function parseTags(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}
