from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from ..adaptation.models import (
    BeatResolution,
    PageCandidate,
    PanelCandidate,
    SceneCandidate,
    StoryboardDocument,
    StoryboardRequest,
)
from ..adaptation.text_model import (
    ModelCandidate,
    SecretReader,
    TextModelConfiguration,
)
from ..bibles.models import (
    BibleDraftBundle,
    BibleDraftRequest,
    CharacterBibleDocument,
    CharacterProfile,
    StyleBibleDocument,
)
from ..prompting.models import (
    CharacterTagDraftBundle,
    CharacterTagGenerationRequest,
    CharacterTagSetDraft,
    PanelPromptDraft,
    PromptCharacterBlockDraft,
    PromptDraftBundleDocument,
    PromptGenerationRequest,
)

PAGE_COUNT = 10
PROJECT_TITLE = "雨夜来信 - 十页短篇"
TEXT_MODEL_BASE_URL = "https://acceptance.local.invalid/v1"
TEXT_MODEL_NAME = "manga-maker-deterministic-acceptance-v1"

PAGE_BEATS: tuple[dict[str, Any], ...] = (
    {
        "turning_point": "林夏在祖母旧屋的门缝里发现一封写给自己的信",
        "purpose": "建立雨夜旧屋与神秘来信",
        "shot": "wide shot",
        "narration": "暴雨之夜，旧屋里有一封等了林夏十年的信。",
        "sfx": "哗啦",
        "visual": [
            "old countryside house exterior",
            "heavy rain at night",
            "warm light through one window",
        ],
        "action": "stands beneath a transparent umbrella and looks toward the lit old house",
        "expression": "cautious expression",
    },
    {
        "turning_point": "信纸上的第一句引她走向停摆的老钟",
        "purpose": "呈现信件与第一个线索",
        "shot": "medium shot",
        "narration": "信上只有一句话 - 去找停在十一点四十分的钟。",
        "sfx": "滴答",
        "visual": [
            "dim wooden hallway",
            "antique wall clock stopped at eleven forty",
            "rain streaks on window",
        ],
        "action": "holds an opened cream letter and studies an antique wall clock",
        "expression": "focused expression",
    },
    {
        "turning_point": "钟后的暗格藏着一把黄铜钥匙",
        "purpose": "揭示黄铜钥匙",
        "shot": "close-up",
        "narration": "钟后藏着一把小钥匙，齿纹像一只飞鸟。",
        "sfx": "咔",
        "visual": [
            "secret compartment behind antique clock",
            "small brass key shaped like a flying bird",
        ],
        "action": "reaches into the hidden compartment and lifts a tiny brass key",
        "expression": "surprised expression",
    },
    {
        "turning_point": "钥匙打开阁楼中贴着月亮标记的木箱",
        "purpose": "进入阁楼并打开木箱",
        "shot": "low angle",
        "narration": "阁楼最深处，月亮标记的木箱应声而开。",
        "sfx": "吱呀",
        "visual": [
            "dusty attic under roof beams",
            "old wooden chest with a crescent moon mark",
            "lightning outside",
        ],
        "action": "kneels beside the crescent-marked chest and turns the brass key",
        "expression": "determined expression",
    },
    {
        "turning_point": "木箱里只有一张祖母年轻时画的车站速写",
        "purpose": "由速写引出车站线索",
        "shot": "over-the-shoulder shot",
        "narration": "箱中没有珠宝，只有一张雨中车站的速写。",
        "sfx": "沙沙",
        "visual": [
            "old sketchbook page showing a rural railway platform in rain",
            "pressed white flower beside it",
        ],
        "action": (
            "holds the old station sketch near the attic window "
            "and compares its crescent mark"
        ),
        "expression": "thoughtful expression",
    },
    {
        "turning_point": "林夏循着速写来到废弃车站的三号长椅",
        "purpose": "从旧屋转场到雨夜车站",
        "shot": "wide shot",
        "narration": "她穿过雨幕，找到早已停运的旧车站。",
        "sfx": "轰隆",
        "visual": [
            "abandoned rural train station at night",
            "heavy rain",
            "platform number three",
            "empty tracks and fog",
        ],
        "action": "walks alone along platform three with the sketch held beneath her umbrella",
        "expression": "resolute expression",
    },
    {
        "turning_point": "长椅下的铁盒中存着十年前未寄出的明信片",
        "purpose": "找到祖母留下的第二封文字",
        "shot": "medium shot",
        "narration": "铁盒里，是祖母从未寄出的十张明信片。",
        "sfx": "当",
        "visual": [
            "weathered green bench on abandoned platform",
            "small tin box under it",
            "scattered vintage postcards",
        ],
        "action": "crouches by the bench and opens a tin box filled with vintage postcards",
        "expression": "tender startled expression",
    },
    {
        "turning_point": "明信片拼成一幅祖孙在海边牵手的完整画面",
        "purpose": "让线索汇聚为共同记忆",
        "shot": "top-down shot",
        "narration": "十张背面拼在一起，是她们曾约好再去的海。",
        "sfx": "啪嗒",
        "visual": [
            "ten vintage postcards arranged into one seaside panorama",
            "station bench",
            "rain drops",
            "warm lantern light",
        ],
        "action": "arranges ten postcards into a complete seaside panorama with careful hands",
        "expression": "tearful soft smile",
    },
    {
        "turning_point": "最后一张明信片告诉她告别不是遗弃",
        "purpose": "完成情绪真相揭示",
        "shot": "close-up portrait",
        "narration": "最后一张写着 - 离开不是遗弃，爱会替我陪你走下去。",
        "sfx": "沙沙",
        "visual": [
            "rain easing beyond station roof",
            "pale dawn on horizon",
            "postcard held close to heart",
        ],
        "action": "presses the final postcard to her chest as the rain begins to stop",
        "expression": "crying with relieved smile",
    },
    {
        "turning_point": "清晨的第一班列车驶过，林夏带着明信片走向新的一天",
        "purpose": "以清晨与前行收束短篇",
        "shot": "cinematic wide shot",
        "narration": "天亮时，她终于读懂了那封迟到十年的告别。",
        "sfx": "呜——",
        "visual": [
            "abandoned station at sunrise after rain",
            "distant train passing",
            "puddles reflecting bright sky",
        ],
        "action": (
            "walks away from the station toward sunrise "
            "carrying the postcards in a small tin box"
        ),
        "expression": "peaceful hopeful expression",
    },
)


class TenPageAcceptanceTextModel:
    """Deterministic local authoring provider used only by the explicit acceptance runner."""

    def __init__(
        self,
        configuration: TextModelConfiguration,
        secret_reader: SecretReader,
    ) -> None:
        self.configuration = configuration
        self.secret_reader = secret_reader

    async def validate_configuration(self) -> bool:
        self._require_profile()
        return True

    async def generate_storyboard(
        self,
        request: StoryboardRequest,
    ) -> ModelCandidate[StoryboardDocument]:
        self._require_profile()
        scene_id = uuid4()
        document = StoryboardDocument(
            schema_version="1.0",
            storyboard_id=uuid4(),
            chapter_version=request.chapter_version,
            beat_resolutions=[
                BeatResolution(
                    beat_id=beat.beat_id,
                    status="represented",
                    page_numbers=list(range(1, PAGE_COUNT + 1)),
                )
                for beat in request.story_beats
            ],
            scenes=[
                SceneCandidate(
                    scene_id=scene_id,
                    order=1,
                    title="雨夜来信",
                    location="祖母旧屋与废弃车站",
                    time_of_day="雨夜至清晨",
                    summary="林夏沿着祖母留下的线索，读懂一封迟到十年的告别。",
                    beat_ids=[beat.beat_id for beat in request.story_beats],
                )
            ],
            pages=[
                PageCandidate(
                    page_id=uuid4(),
                    page_number=page_number,
                    turning_point=str(beat["turning_point"]),
                    scene_ids=[scene_id],
                    panels=[
                        PanelCandidate(
                            panel_id=uuid4(),
                            order=1,
                            purpose=str(beat["purpose"]),
                            shot=str(beat["shot"]),
                            characters=["林夏"],
                            dialogue=[],
                            narration=[str(beat["narration"])],
                            sfx=[str(beat["sfx"])],
                            visual_prompt=(
                                "black and white manga, consistent adult Chinese heroine, "
                                f"{', '.join(cast(list[str], beat['visual']))}, no text"
                            ),
                            negative_prompt=(
                                "color, photorealistic, text, watermark, logo, speech bubble, "
                                "extra person, duplicate character"
                            ),
                            source_anchor_ids=[
                                source_beat.anchor_id for source_beat in request.story_beats
                            ],
                        )
                    ],
                )
                for page_number, beat in enumerate(PAGE_BEATS, start=1)
            ],
        )
        return self._candidate(document, "storyboard-ten-pages-1.0")

    async def generate_bible_bundle(
        self,
        request: BibleDraftRequest,
    ) -> ModelCandidate[BibleDraftBundle]:
        self._require_profile()
        character = CharacterProfile(
            character_id=uuid4(),
            name="林夏",
            narrative_role="主角，沿祖母线索完成告别的年轻插画师",
            age_range="24-28 岁",
            face_shape="鹅蛋脸，细直眉，深色杏眼",
            hair="齐肩黑色波波头，右侧分刘海",
            body_type="中等身高，清瘦体型",
            outfit=["白色及膝风衣", "深色高领衫", "深色长裤", "短靴"],
            signature_features=["左眼下方小痣", "透明长柄雨伞", "黄铜飞鸟钥匙"],
            forbidden_changes=["头发不得超过肩部", "眼下小痣不得消失", "风衣不得变为深色"],
            expression_range=["警觉", "专注", "惊讶", "释然", "含泪微笑"],
            positive_prompt_fragment=(
                "1girl, adult Chinese woman, shoulder-length black bob hair, side-swept bangs, "
                "beauty mark under left eye, white knee-length trench coat, dark turtleneck"
            ),
            negative_prompt_fragment=(
                "long hair, blonde hair, missing beauty mark, black coat, school uniform, child"
            ),
        )
        document = BibleDraftBundle(
            schema_version="1.0",
            character_bible=CharacterBibleDocument(
                schema_version="1.0",
                character_bible_id=request.character_bible_id,
                storyboard_version_id=request.storyboard_version_id,
                characters=[character],
                notes="十页验收短篇 - 单主角外观必须跨页稳定。",
            ),
            style_bible=StyleBibleDocument(
                schema_version="1.0",
                style_bible_id=request.style_bible_id,
                storyboard_version_id=request.storyboard_version_id,
                summary="电影感黑白悬疑漫画，雨夜冷调到清晨明调",
                line_art="clean expressive ink line art, controlled detail",
                screentone="cinematic grayscale screentone, wet surface reflections",
                lighting="high contrast rainy-night lighting that softens toward dawn",
                background_density=(
                    "detailed establishing backgrounds with readable focal hierarchy"
                ),
                whitespace="clear subject silhouette and restrained negative space",
                camera_language="cinematic manga composition with one decisive image per page",
                positive_prompt_fragment=(
                    "masterpiece, black and white manga, expressive ink line art, "
                    "cinematic framing, "
                    "grayscale screentone, dramatic rain lighting"
                ),
                negative_prompt_fragment=(
                    "color, photorealistic, 3d render, text, watermark, logo, "
                    "speech bubble, bad anatomy"
                ),
                prohibited_elements=["彩色", "照片感", "水印", "画面内文字", "额外角色"],
            ),
        )
        return self._candidate(document, "bibles-ten-pages-1.0")

    async def generate_character_tags(
        self,
        request: CharacterTagGenerationRequest,
    ) -> ModelCandidate[CharacterTagDraftBundle]:
        self._require_profile()
        document = CharacterTagDraftBundle(
            schema_version="1.0",
            storyboard_version_id=request.storyboard_version_id,
            character_bible_version_id=request.character_bible_version_id,
            style_bible_version_id=request.style_bible_version_id,
            tag_sets=[
                CharacterTagSetDraft(
                    tag_set_id=request.target_tag_set_ids[str(character.character_id)],
                    character_id=character.character_id,
                    character_name=character.name,
                    fixed_tags=[
                        "1girl",
                        "adult Chinese woman",
                        "shoulder-length black bob hair",
                        "side-swept bangs",
                        "beauty mark under left eye",
                        "white knee-length trench coat",
                        "dark turtleneck",
                        "dark pants",
                    ],
                    negative_tags=[
                        "long hair",
                        "blonde hair",
                        "missing beauty mark",
                        "black coat",
                        "school uniform",
                        "child",
                    ],
                    rationale="冻结林夏的年龄、发型、标志痣和白色风衣，保证十页身份连续。",
                )
                for character in request.character_bible.characters
            ],
        )
        return self._candidate(document, "character-tags-ten-pages-1.0")

    async def generate_prompt_bundle(
        self,
        request: PromptGenerationRequest,
    ) -> ModelCandidate[PromptDraftBundleDocument]:
        self._require_profile()
        character_by_name = {
            character.name.casefold(): character.character_id
            for character in request.character_bible.characters
        }
        tag_id_by_character = {
            str(tag.character_id): tag.tag_set_id for tag in request.character_tags.tag_sets
        }
        layout_pages = cast(list[dict[str, Any]], request.layout_snapshot["pages"])
        frame_by_panel = {
            str(frame["frame"]["panel_id"]): frame["frame"]
            for page in layout_pages
            for frame in cast(list[dict[str, Any]], page["frames"])
        }
        beat_by_page = {
            page_number: beat for page_number, beat in enumerate(PAGE_BEATS, start=1)
        }
        packages: list[PanelPromptDraft] = []
        for page in request.storyboard.pages:
            beat = beat_by_page[page.page_number]
            for panel in page.panels:
                character_id = character_by_name[panel.characters[0].casefold()]
                frame = frame_by_panel[str(panel.panel_id)]
                packages.append(
                    PanelPromptDraft(
                        prompt_package_id=request.target_prompt_package_ids[str(panel.panel_id)],
                        panel_id=panel.panel_id,
                        base_visual_tags=[
                            "masterpiece",
                            "black and white manga",
                            "single panel comic illustration",
                            str(panel.shot),
                            *cast(list[str], beat["visual"]),
                        ],
                        character_blocks=[
                            PromptCharacterBlockDraft(
                                character_id=character_id,
                                tag_set_id=tag_id_by_character[str(character_id)],
                                variable_tags=[str(beat["expression"])],
                                negative_tags=[
                                    "extra limbs",
                                    "bad hands",
                                    "duplicate character",
                                ],
                                action=str(beat["action"]),
                                order=0,
                                center=frame["character_positions"][0]["center"],
                            )
                        ],
                        style_tags=[
                            "expressive ink line art",
                            "cinematic grayscale screentone",
                            "dramatic rain lighting",
                            "detailed background",
                        ],
                        negative_tags=[
                            "color",
                            "photorealistic",
                            "3d render",
                            "text",
                            "watermark",
                            "logo",
                            "speech bubble",
                            "panel border",
                        ],
                        continuity_tags=[
                            "same heroine design",
                            "same white trench coat",
                            f"story page {page.page_number} of {PAGE_COUNT}",
                        ],
                    )
                )
        document = PromptDraftBundleDocument(
            schema_version="1.0",
            storyboard_version_id=request.storyboard_version_id,
            character_tag_bundle_version_id=request.character_tag_bundle_version_id,
            packages=packages,
        )
        return self._candidate(document, "panel-plan-ten-pages-v2")

    def _require_profile(self) -> None:
        self.secret_reader(self.configuration.credential_profile_id)

    def _candidate(self, document: Any, template: str) -> ModelCandidate[Any]:
        return ModelCandidate(
            document=document,
            provider="manga-maker-local-deterministic",
            model=self.configuration.model,
            endpoint_host="local-acceptance",
            prompt_template_version=template,
            response_sha256="d" * 64,
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
            repair_attempts=0,
        )
