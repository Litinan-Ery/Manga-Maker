from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..adaptation.contracts import StoryboardVersionRefV1

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class LayoutContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class NormalizedPoint(LayoutContract):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class NormalizedRect(LayoutContract):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def remains_in_bounds(self) -> NormalizedRect:
        tolerance = 1e-9
        if self.x + self.width > 1 + tolerance or self.y + self.height > 1 + tolerance:
            raise ValueError("normalized rectangle must remain within its parent")
        return self


class CanvasSpec(LayoutContract):
    width: int = Field(ge=512, le=4096)
    height: int = Field(ge=512, le=16_000)


class CharacterPosition(LayoutContract):
    character_id: UUID
    center: NormalizedPoint
    prominence: Literal["primary", "secondary", "background"]


class TextSafeZone(LayoutContract):
    zone_id: UUID
    kind: Literal["dialogue", "narration", "sfx", "any"]
    rect: NormalizedRect


class FrameSpec(LayoutContract):
    frame_id: UUID
    parent_frame_id: UUID | None = None
    panel_id: UUID | None = None
    order: int | None = Field(default=None, ge=1, le=100)
    rect: NormalizedRect
    aspect_ratio: float = Field(gt=0, le=10)
    shot_scale: Literal[
        "extreme_close_up",
        "close_up",
        "medium",
        "full",
        "wide",
        "establishing",
    ]
    focal_point: NormalizedPoint
    character_positions: list[CharacterPosition] = Field(default_factory=list, max_length=20)
    text_safe_zones: list[TextSafeZone] = Field(default_factory=list, max_length=20)
    crop_safe_rect: NormalizedRect

    @model_validator(mode="after")
    def leaf_fields_are_coherent(self) -> FrameSpec:
        if self.panel_id is None and self.order is not None:
            raise ValueError("container frames cannot have a reading order")
        if self.panel_id is not None and self.order is None:
            raise ValueError("leaf frames require a reading order")
        character_ids = [position.character_id for position in self.character_positions]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("a character can appear only once in a frame position list")
        zone_ids = [zone.zone_id for zone in self.text_safe_zones]
        if len(zone_ids) != len(set(zone_ids)):
            raise ValueError("text safe zone ids must be unique within a frame")
        return self


class PageLayoutDraft(LayoutContract):
    schema_version: Literal["1.0"] = "1.0"
    page_layout_draft_id: UUID
    version: int = Field(ge=1)
    page_id: UUID
    page_profile: Literal["print_portrait_2_3", "digital_portrait_2_3", "vertical_strip"]
    canvas: CanvasSpec
    reading_direction: Literal["ltr_ttb", "rtl_ttb", "ttb"]
    frames: list[FrameSpec] = Field(min_length=1, max_length=100)
    content_sha256: Sha256
    approved_content_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def valid_frame_tree(self) -> PageLayoutDraft:
        by_id = {frame.frame_id: frame for frame in self.frames}
        if len(by_id) != len(self.frames):
            raise ValueError("frame ids must be unique")

        children: dict[UUID, list[UUID]] = {frame_id: [] for frame_id in by_id}
        for frame in self.frames:
            if frame.parent_frame_id is not None:
                if frame.parent_frame_id not in by_id:
                    raise ValueError("frame parent must exist in the same layout")
                if frame.parent_frame_id == frame.frame_id:
                    raise ValueError("frame cannot be its own parent")
                children[frame.parent_frame_id].append(frame.frame_id)

        roots = [frame.frame_id for frame in self.frames if frame.parent_frame_id is None]
        if len(roots) != 1:
            raise ValueError("a page layout requires exactly one root frame")

        visited: set[UUID] = set()
        active: set[UUID] = set()

        def visit(frame_id: UUID) -> None:
            if frame_id in active:
                raise ValueError("frame hierarchy must be acyclic")
            if frame_id in visited:
                return
            active.add(frame_id)
            for child_id in children[frame_id]:
                visit(child_id)
            active.remove(frame_id)
            visited.add(frame_id)

        visit(roots[0])
        if len(visited) != len(self.frames):
            raise ValueError("every frame must be connected to the root")

        leaf_frames = [frame for frame in self.frames if not children[frame.frame_id]]
        if any(frame.panel_id is None for frame in leaf_frames):
            raise ValueError("every leaf frame must reference a panel")
        if any(frame.panel_id is not None for frame in self.frames if children[frame.frame_id]):
            raise ValueError("container frames cannot reference panels")
        panel_ids = [frame.panel_id for frame in leaf_frames]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("each panel must appear in exactly one leaf frame")
        orders = sorted(frame.order for frame in leaf_frames if frame.order is not None)
        if orders != list(range(1, len(leaf_frames) + 1)):
            raise ValueError("leaf reading order must be contiguous and start at one")
        return self


LayoutOrigin = Literal["planned", "imported_legacy"]
LayoutApprovalState = Literal["active", "stale"]
LayoutApprovalStaleReason = Literal[
    "layout_version_superseded",
    "layout_content_changed",
    "storyboard_binding_changed",
]


class LayoutVersionSnapshotV1(LayoutContract):
    """Public, provider-neutral view of one immutable layout version."""

    contract_version: Literal["1.0"] = "1.0"
    page_layout_draft_version_id: UUID
    project_id: UUID
    chapter_id: UUID
    revision: int = Field(ge=1)
    origin: LayoutOrigin
    storyboard: StoryboardVersionRefV1 | None
    approved_panel_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    legacy_page_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    layout: PageLayoutDraft
    snapshot_sha256: Sha256
    created_at: datetime
    external_requests_started: Literal[0] = 0

    @model_validator(mode="after")
    def identifiers_and_origin_are_coherent(self) -> LayoutVersionSnapshotV1:
        if len(set(self.approved_panel_ids)) != len(self.approved_panel_ids):
            raise ValueError("approved panel ids must be unique")
        if self.layout.version != self.revision:
            raise ValueError("layout version and revision must match")
        if self.origin == "planned" and self.storyboard is None:
            raise ValueError("planned layouts require an approved storyboard binding")
        if self.origin == "planned" and self.legacy_page_version_id is not None:
            raise ValueError("planned layouts cannot reference a legacy page version")
        if self.origin == "imported_legacy" and self.legacy_page_version_id is None:
            raise ValueError("legacy layouts require their source page version")
        return self


class LayoutApprovalV1(LayoutContract):
    """Immutable approval binding plus its query-time staleness state."""

    contract_version: Literal["1.0"] = "1.0"
    approval_id: UUID
    project_id: UUID
    page_layout_draft_id: UUID
    page_layout_draft_version_id: UUID
    layout_version: int = Field(ge=1)
    layout_content_sha256: Sha256
    storyboard: StoryboardVersionRefV1
    dimension_selection_sha256s: tuple[Sha256, ...] = Field(default_factory=tuple)
    approval_sha256: Sha256
    state: LayoutApprovalState
    stale_reasons: tuple[LayoutApprovalStaleReason, ...]
    created_at: datetime
    external_requests_started: Literal[0] = 0

    @model_validator(mode="after")
    def state_matches_reasons(self) -> LayoutApprovalV1:
        if self.state == "active" and self.stale_reasons:
            raise ValueError("an active approval cannot have stale reasons")
        if self.state == "stale" and not self.stale_reasons:
            raise ValueError("a stale approval requires at least one reason")
        return self


class LayoutPageRequirementV1(LayoutContract):
    """Exact storyboard page coverage required by a generation consumer."""

    page_id: UUID
    panel_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def panels_are_unique(self) -> LayoutPageRequirementV1:
        if len(set(self.panel_ids)) != len(self.panel_ids):
            raise ValueError("layout page requirement panel ids must be unique")
        return self


class PixelDimensions(LayoutContract):
    width: int = Field(ge=64, le=16_384)
    height: int = Field(ge=64, le=16_384)


class DimensionCapabilityCandidateV1(LayoutContract):
    candidate_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    dimensions: PixelDimensions
    pixel_limit: int = Field(ge=4_096, le=268_435_456)
    cost_rank: int = Field(ge=0, le=1_000)

    @model_validator(mode="after")
    def dimensions_fit_declared_limit(self) -> DimensionCapabilityCandidateV1:
        if self.dimensions.width * self.dimensions.height > self.pixel_limit:
            raise ValueError("dimension candidate exceeds its declared pixel limit")
        return self


class DimensionCapabilitySetV1(LayoutContract):
    contract_version: Literal["1.0"] = "1.0"
    capability_snapshot_id: str = Field(min_length=1, max_length=128)
    mapping_version: str = Field(min_length=1, max_length=64)
    candidates: tuple[DimensionCapabilityCandidateV1, ...] = Field(
        min_length=1,
        max_length=100,
    )
    content_sha256: Sha256

    @model_validator(mode="after")
    def candidate_keys_are_unique(self) -> DimensionCapabilitySetV1:
        keys = [candidate.candidate_key for candidate in self.candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("dimension capability candidate keys must be unique")
        return self


class DimensionCandidateScoreV1(LayoutContract):
    candidate_key: str
    dimensions: PixelDimensions
    aspect_ratio_error: float = Field(ge=0)
    crop_safe_risk: float = Field(ge=0, le=1)
    expected_crop_ratio: float = Field(ge=0, le=1)
    target_pixel_delta: int = Field(ge=0)
    cost_rank: int = Field(ge=0)
    crop_safe_satisfied: bool


class DimensionSelectionV1(LayoutContract):
    """Provider-neutral result contract; selection logic belongs to MM-032."""

    contract_version: Literal["1.0"] = "1.0"
    status: Literal["selected"] = "selected"
    dimension_selection_id: UUID
    page_layout_draft_version_id: UUID
    frame_id: UUID
    capability_snapshot_id: str = Field(min_length=1, max_length=128)
    capability_snapshot_sha256: Sha256
    rule_version: str = Field(min_length=1, max_length=64)
    selected_candidate_key: str = Field(min_length=1, max_length=128)
    selected: PixelDimensions
    target_aspect_ratio: float = Field(gt=0, le=10)
    expected_crop_ratio: float = Field(ge=0, le=1)
    ranked_candidates: tuple[DimensionCandidateScoreV1, ...] = Field(
        min_length=1,
        max_length=100,
    )
    selection_reason: str = Field(min_length=1, max_length=500)
    content_sha256: Sha256

    @model_validator(mode="after")
    def selected_candidate_is_present_and_safe(self) -> DimensionSelectionV1:
        selected = next(
            (
                score
                for score in self.ranked_candidates
                if score.candidate_key == self.selected_candidate_key
            ),
            None,
        )
        if selected is None or selected.dimensions != self.selected:
            raise ValueError("selected candidate must occur in the ranked candidates")
        if not selected.crop_safe_satisfied:
            raise ValueError("selected dimensions must satisfy the crop-safe threshold")
        if self.expected_crop_ratio != selected.expected_crop_ratio:
            raise ValueError("selection crop ratio must match its ranked candidate")
        return self


class ApprovedFrameSnapshotV1(LayoutContract):
    """Frozen, provider-neutral frame plus its deterministic size decision."""

    contract_version: Literal["1.0"] = "1.0"
    frame: FrameSpec
    frame_content_sha256: Sha256
    dimension_selection: DimensionSelectionV1

    @model_validator(mode="after")
    def frame_and_selection_match(self) -> ApprovedFrameSnapshotV1:
        if self.frame.frame_id != self.dimension_selection.frame_id:
            raise ValueError("approved frame and dimension selection must match")
        if self.frame.panel_id is None:
            raise ValueError("approved generation frames must reference a panel")
        return self


class ApprovedPageLayoutSnapshotV1(LayoutContract):
    """One active LayoutApproval with all leaf-frame generation inputs."""

    contract_version: Literal["1.0"] = "1.0"
    version: LayoutVersionSnapshotV1
    approval: LayoutApprovalV1
    frames: tuple[ApprovedFrameSnapshotV1, ...] = Field(min_length=1, max_length=100)
    validation_rule_version: str = Field(min_length=1, max_length=64)
    content_sha256: Sha256
    external_requests_started: Literal[0] = 0

    @model_validator(mode="after")
    def approval_and_frames_match(self) -> ApprovedPageLayoutSnapshotV1:
        if self.approval.state != "active":
            raise ValueError("approved page snapshot requires an active approval")
        if (
            self.version.page_layout_draft_version_id
            != self.approval.page_layout_draft_version_id
        ):
            raise ValueError("layout version and approval must match")
        panel_ids = [item.frame.panel_id for item in self.frames]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("approved page frames must have unique panel ids")
        if set(panel_ids) != set(self.version.approved_panel_ids):
            raise ValueError("approved page frames must cover the storyboard exactly")
        return self


class ApprovedChapterLayoutSnapshotV1(LayoutContract):
    """Generation gate snapshot for every page in one approved storyboard."""

    contract_version: Literal["1.0"] = "1.0"
    project_id: UUID
    chapter_id: UUID
    storyboard: StoryboardVersionRefV1
    pages: tuple[ApprovedPageLayoutSnapshotV1, ...] = Field(
        min_length=1,
        max_length=64,
    )
    content_sha256: Sha256
    external_requests_started: Literal[0] = 0

    @model_validator(mode="after")
    def pages_are_unique_and_bound(self) -> ApprovedChapterLayoutSnapshotV1:
        if not self.storyboard.approved:
            raise ValueError("chapter layout snapshot requires an approved storyboard")
        page_ids = [page.version.layout.page_id for page in self.pages]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("chapter layout snapshot page ids must be unique")
        if any(
            page.version.project_id != self.project_id
            or page.version.chapter_id != self.chapter_id
            or page.approval.storyboard != self.storyboard
            for page in self.pages
        ):
            raise ValueError("chapter layout snapshot bindings do not match")
        return self


class DimensionSelectionFailureV1(LayoutContract):
    contract_version: Literal["1.0"] = "1.0"
    status: Literal["unsatisfied"] = "unsatisfied"
    page_layout_draft_version_id: UUID
    frame_id: UUID
    capability_snapshot_id: str = Field(min_length=1, max_length=128)
    capability_snapshot_sha256: Sha256
    rule_version: str = Field(min_length=1, max_length=64)
    target_aspect_ratio: float = Field(gt=0, le=10)
    failure_reason: Literal["no_candidate_preserves_crop_safe_rect"]
    ranked_candidates: tuple[DimensionCandidateScoreV1, ...] = Field(
        min_length=1,
        max_length=100,
    )
    content_sha256: Sha256


class DimensionSelectionRequestV1(LayoutContract):
    page_layout_draft_version_id: UUID
    frame: FrameSpec
    capabilities: DimensionCapabilitySetV1
    target_pixels: int = Field(ge=4_096, le=268_435_456)
    max_crop_safe_risk: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def frame_is_a_panel_leaf(self) -> DimensionSelectionRequestV1:
        if self.frame.panel_id is None:
            raise ValueError("dimension selection requires a panel frame")
        return self


class ReadingOrderConstraintV1(LayoutContract):
    before_frame_id: UUID
    after_frame_id: UUID

    @model_validator(mode="after")
    def frames_are_distinct(self) -> ReadingOrderConstraintV1:
        if self.before_frame_id == self.after_frame_id:
            raise ValueError("reading order constraint cannot point to itself")
        return self


class LayoutValidationRulesV1(LayoutContract):
    rule_version: Literal["layout-validator-v1"] = "layout-validator-v1"
    minimum_leaf_area_ratio: float = Field(default=0.01, gt=0, le=1)
    minimum_gutter_ratio: float = Field(default=0.01, ge=0, le=0.25)
    overlap_tolerance_ratio: float = Field(default=1e-9, ge=0, le=0.01)


class LayoutValidationRequestV1(LayoutContract):
    layout: PageLayoutDraft
    approved_panel_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    reading_order_constraints: tuple[ReadingOrderConstraintV1, ...] = Field(
        default_factory=tuple,
        max_length=1_000,
    )
    rules: LayoutValidationRulesV1 = Field(default_factory=LayoutValidationRulesV1)

    @model_validator(mode="after")
    def references_are_unique(self) -> LayoutValidationRequestV1:
        if len(set(self.approved_panel_ids)) != len(self.approved_panel_ids):
            raise ValueError("approved panel ids must be unique")
        edges = [
            (constraint.before_frame_id, constraint.after_frame_id)
            for constraint in self.reading_order_constraints
        ]
        if len(edges) != len(set(edges)):
            raise ValueError("reading order constraints must be unique")
        return self


class LayoutValidationFindingV1(LayoutContract):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    path: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=500)
    frame_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)


class LayoutValidationResultV1(LayoutContract):
    contract_version: Literal["1.0"] = "1.0"
    rule_version: str
    layout_content_sha256: Sha256
    valid: bool
    findings: tuple[LayoutValidationFindingV1, ...]
    external_requests_started: Literal[0] = 0

    @model_validator(mode="after")
    def validity_matches_findings(self) -> LayoutValidationResultV1:
        if self.valid == bool(self.findings):
            raise ValueError("layout validity must be the inverse of findings")
        return self


class CreateLayoutDraftCommandV1(LayoutContract):
    project_id: UUID
    chapter_id: UUID
    storyboard: StoryboardVersionRefV1
    approved_panel_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    draft: PageLayoutDraft

    @model_validator(mode="after")
    def storyboard_and_panels_are_approved(self) -> CreateLayoutDraftCommandV1:
        if not self.storyboard.approved:
            raise ValueError("layout creation requires an approved storyboard")
        if len(set(self.approved_panel_ids)) != len(self.approved_panel_ids):
            raise ValueError("approved panel ids must be unique")
        return self


class ImportLegacyLayoutCommandV1(LayoutContract):
    project_id: UUID
    chapter_id: UUID
    legacy_page_version_id: str = Field(min_length=1, max_length=128)
    panel_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    draft: PageLayoutDraft

    @model_validator(mode="after")
    def legacy_panels_are_unique(self) -> ImportLegacyLayoutCommandV1:
        if len(set(self.panel_ids)) != len(self.panel_ids):
            raise ValueError("legacy panel ids must be unique")
        return self


class SaveLayoutDraftCommandV1(LayoutContract):
    project_id: UUID
    page_layout_draft_id: UUID
    expected_revision: int = Field(ge=1)
    storyboard: StoryboardVersionRefV1
    approved_panel_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    draft: PageLayoutDraft

    @model_validator(mode="after")
    def storyboard_and_panels_are_approved(self) -> SaveLayoutDraftCommandV1:
        if not self.storyboard.approved:
            raise ValueError("layout saving requires an approved storyboard")
        if len(set(self.approved_panel_ids)) != len(self.approved_panel_ids):
            raise ValueError("approved panel ids must be unique")
        return self


class ValidateLayoutCommandV1(LayoutContract):
    project_id: UUID
    page_layout_draft_version_id: UUID
    expected_revision: int = Field(ge=1)
    layout_content_sha256: Sha256
    storyboard: StoryboardVersionRefV1
    dimension_capabilities: DimensionCapabilitySetV1
    target_pixels: int = Field(ge=4_096, le=268_435_456)
    max_crop_safe_risk: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def storyboard_is_approved(self) -> ValidateLayoutCommandV1:
        if not self.storyboard.approved:
            raise ValueError("layout validation requires an approved storyboard")
        return self


class LayoutApprovalValidationV1(LayoutContract):
    contract_version: Literal["1.0"] = "1.0"
    page_layout_draft_version_id: UUID
    layout: LayoutValidationResultV1
    dimension_outcomes: tuple[
        DimensionSelectionV1 | DimensionSelectionFailureV1,
        ...,
    ]
    valid: bool
    failure_paths: tuple[str, ...]
    external_requests_started: Literal[0] = 0


class ApproveLayoutCommandV1(LayoutContract):
    project_id: UUID
    page_layout_draft_id: UUID
    page_layout_draft_version_id: UUID
    expected_revision: int = Field(ge=1)
    layout_content_sha256: Sha256
    storyboard: StoryboardVersionRefV1
    dimension_selections: tuple[DimensionSelectionV1, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def storyboard_is_approved(self) -> ApproveLayoutCommandV1:
        if not self.storyboard.approved:
            raise ValueError("layout approval requires an approved storyboard")
        frame_ids = [selection.frame_id for selection in self.dimension_selections]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("layout approval dimensions must have one selection per frame")
        if any(
            selection.page_layout_draft_version_id != self.page_layout_draft_version_id
            for selection in self.dimension_selections
        ):
            raise ValueError("dimension selections must bind the approved layout version")
        return self
