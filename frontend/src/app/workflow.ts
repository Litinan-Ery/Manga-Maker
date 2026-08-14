import { ADAPTATION_FEATURE } from "../features/adaptation";
import { ASSET_CATALOG_FEATURE } from "../features/asset_catalog";
import { COMPOSITION_FEATURE } from "../features/composition";
import { EXPORTING_FEATURE } from "../features/exporting";
import { LAYOUT_FEATURE } from "../features/layout";
import { PRODUCTION_FEATURE } from "../features/production";
import { PROMPTING_FEATURE } from "../features/prompting";
import { REVIEW_FEATURE } from "../features/review";

export const V03_FEATURE_ORDER = [
  ADAPTATION_FEATURE,
  LAYOUT_FEATURE,
  PROMPTING_FEATURE,
  PRODUCTION_FEATURE,
  REVIEW_FEATURE,
  COMPOSITION_FEATURE,
  ASSET_CATALOG_FEATURE,
  EXPORTING_FEATURE,
] as const;
