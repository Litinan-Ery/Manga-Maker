export const PROMPTING_FEATURE = "prompting" as const;
export { PromptInspectorView } from "./Inspector";
export type { CharacterDraftPatch, PromptInspectorViewProps } from "./Inspector";
export { createPromptInspectorHttpClient } from "./client";
export type {
  PromptInspector,
  PromptInspectorClient,
  PromptInspectorPanel,
} from "./client";
