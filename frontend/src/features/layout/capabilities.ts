import type { DimensionCapabilitySet } from "./client";

export const LOCAL_DIMENSION_CAPABILITIES: DimensionCapabilitySet = {
  contract_version: "1.0",
  capability_snapshot_id: "novelai-opus-zero-anlas-2026-08-14",
  mapping_version: "novelai-opus-zero-anlas-dimensions-v1",
  candidates: [
    {
      candidate_key: "landscape-1216x832",
      dimensions: { width: 1216, height: 832 },
      pixel_limit: 1_048_576,
      cost_rank: 0,
    },
    {
      candidate_key: "portrait-832x1216",
      dimensions: { width: 832, height: 1216 },
      pixel_limit: 1_048_576,
      cost_rank: 0,
    },
    {
      candidate_key: "square-1024x1024",
      dimensions: { width: 1024, height: 1024 },
      pixel_limit: 1_048_576,
      cost_rank: 0,
    },
  ],
  content_sha256: "cab53f2203232177dd4ac3a977c9860ecca2392ddd570d62ea5fc48a94951df8",
};
