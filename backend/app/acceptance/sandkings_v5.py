from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from ..adaptation.models import (
    BeatResolution,
    PageCandidate,
    PanelCandidate,
    SceneCandidate,
    StoryboardDocument,
    StoryboardRequest,
)
from ..adaptation.text_model import ModelCandidate, SecretReader, TextModelConfiguration
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

PAGE_COUNT = 12
PROJECT_TITLE = "沙王 - NovelAI V5 完整漫画验收"
TEXT_MODEL_BASE_URL = "https://acceptance.local.invalid/v1"
TEXT_MODEL_NAME = "manga-maker-sandkings-v5-acceptance-v1"
_SECTION_PATTERN = re.compile(r"^##\s*沙王\uFF08([123])/3\uFF09\s*$")
_H2_PATTERN = re.compile(r"^##\s+")
_REQUIRED_STORY_MARKERS = ("西蒙·克雷斯", "贾拉·沃", "沙王", "橙色", "四只胳膊")


@dataclass(frozen=True, slots=True)
class ExtractedSandkingsSource:
    source_path: Path
    source_sha256: str
    extracted_sha256: str
    start_line: int
    end_line: int
    text: str

    def manifest(self) -> dict[str, str | int]:
        return {
            "source_path": str(self.source_path),
            "source_sha256": self.source_sha256,
            "extracted_sha256": self.extracted_sha256,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass(frozen=True, slots=True)
class CharacterDesign:
    name: str
    aliases: tuple[str, ...]
    narrative_role: str
    age_range: str
    face_shape: str
    hair: str
    body_type: str
    outfit: tuple[str, ...]
    signature_features: tuple[str, ...]
    forbidden_changes: tuple[str, ...]
    expression_range: tuple[str, ...]
    fixed_tags: tuple[str, ...]
    negative_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SandkingsPageBeat:
    turning_point: str
    purpose: str
    shot: str
    narration: str
    sfx: str
    visual_tags: tuple[str, ...]
    characters: tuple[str, ...]
    actions: tuple[str, ...]
    expressions: tuple[str, ...]
    relationship_action: str | None = None


CHARACTER_DESIGNS: tuple[CharacterDesign, ...] = (
    CharacterDesign(
        name="西蒙·克雷斯",
        aliases=("克雷斯",),
        narrative_role="富有而残酷的异星宠物收藏家，沙王反噬悲剧的中心人物",
        age_range="38-45 岁",
        face_shape="苍白狭长的棱角脸，细眉与深陷灰眼",
        hair="黑色短发向后梳，鬓角带银灰",
        body_type="高挑清瘦，姿态傲慢",
        outfit=("暗红丝绒长外套", "黑色高领衫", "黑色长裤", "金色图章戒指"),
        signature_features=("银灰鬓角", "尖削下颌", "暗红外套", "金色图章戒指"),
        forbidden_changes=("不得变为少年", "不得留长发", "不得移除银灰鬓角", "外套不得变蓝"),
        expression_range=("厌倦", "傲慢兴奋", "暴怒", "惊恐", "绝望"),
        fixed_tags=(
            "adult man",
            "pale angular face",
            "slicked-back short black hair",
            "silver temples",
            "deep-set gray eyes",
            "dark red velvet long coat",
            "black turtleneck",
            "gold signet ring",
        ),
        negative_tags=(
            "teenage boy",
            "long hair",
            "blonde hair",
            "blue coat",
            "missing silver temples",
        ),
    ),
    CharacterDesign(
        name="贾拉·沃",
        aliases=("沃",),
        narrative_role="沃-希德进口商店女店主，沙王知识与警告的提供者",
        age_range="外表约 45-55 岁",
        face_shape="宽阔平静的古铜色脸，金色杏眼",
        hair="剃光头，戴象牙色兜帽",
        body_type="高大健壮，沉稳威严",
        outfit=("象牙色层叠兜帽长袍", "青铜胸针", "深色手套"),
        signature_features=("金色眼睛", "剃光头", "象牙兜帽", "青铜三角胸针"),
        forbidden_changes=("不得出现长发", "不得变为少女", "长袍不得变为红色"),
        expression_range=("神秘微笑", "冷静警告", "严肃专注"),
        fixed_tags=(
            "mature tall woman",
            "bronze skin",
            "shaved head",
            "golden eyes",
            "ivory hooded layered robe",
            "bronze triangular brooch",
            "dark gloves",
        ),
        negative_tags=("young girl", "long hair", "blue eyes", "red robe"),
    ),
    CharacterDesign(
        name="卡茜·穆雷",
        aliases=("卡茜",),
        narrative_role="克雷斯的前情人和道德反对者，试图终止残酷游戏",
        age_range="30-38 岁",
        face_shape="心形脸，绿色眼睛，神情坚定",
        hair="赤褐色波浪短发至下巴",
        body_type="中等身高，动作果断",
        outfit=("深绿色长袖晚礼服", "银色月牙项链", "黑色短靴"),
        signature_features=("赤褐短发", "绿色礼服", "银色月牙项链"),
        forbidden_changes=("头发不得过肩", "礼服不得变红", "不得移除月牙项链"),
        expression_range=("厌恶", "愤怒", "坚定", "震惊"),
        fixed_tags=(
            "adult woman",
            "chin-length wavy auburn hair",
            "green eyes",
            "dark green long-sleeved evening dress",
            "silver crescent necklace",
            "black ankle boots",
        ),
        negative_tags=("child", "long hair", "blonde hair", "red dress"),
    ),
    CharacterDesign(
        name="莉珊德拉",
        aliases=(),
        narrative_role="受雇清剿沙王的专业佣兵队长",
        age_range="35-45 岁",
        face_shape="深棕色方脸，琥珀眼，左眉有短疤",
        hair="银白色寸头",
        body_type="高大结实，战术姿态稳定",
        outfit=("芥末黄色装甲防护服", "黑色胸甲", "重型防护靴"),
        signature_features=("银白寸头", "左眉短疤", "黄色防护服", "工业激光炮"),
        forbidden_changes=("不得留长发", "防护服不得变白", "左眉疤不得消失"),
        expression_range=("冷静", "警觉", "惊愕", "愤怒"),
        fixed_tags=(
            "muscular adult woman",
            "dark brown skin",
            "silver buzz cut",
            "amber eyes",
            "short scar through left eyebrow",
            "mustard yellow armored hazard suit",
            "black chest armor",
            "heavy utility boots",
        ),
        negative_tags=("child", "long hair", "white suit", "missing eyebrow scar"),
    ),
)


PAGE_BEATS: tuple[SandkingsPageBeat, ...] = (
    SandkingsPageBeat(
        turning_point="克雷斯发现旧宠物死亡，决定寻找更刺激的收藏",
        purpose="建立主角的财富、厌倦与危险欲望",
        shot="cinematic wide shot",
        narration="巴尔德尔星球上，西蒙·克雷斯只为一种东西感到快乐: 支配危险的生命。",
        sfx="嗡——",
        visual_tags=(
            "retrofuturist alien manor",
            "empty exotic animal enclosures",
            "dead aquarium under cold blue light",
            "distant neon city through panoramic window",
        ),
        characters=("西蒙·克雷斯",),
        actions=("stands before the dead aquarium while choosing a new obsession",),
        expressions=("cold bored expression",),
    ),
    SandkingsPageBeat(
        turning_point="贾拉·沃展示四窝沙王与会雕刻神像的城堡",
        purpose="首次揭示沙王的智慧、战争与崇拜能力",
        shot="low angle two-shot",
        narration="彩虹大道尽头，贾拉·沃让他看见四座微型王国——以及四张饥饿的巨口。",
        sfx="沙沙",
        visual_tags=(
            "mysterious alien pet shop",
            "large glass habitat divided into four colored colonies",
            "black red white and orange insect armies",
            "organic sand castles with carved faces",
            "four pulsing maws beneath the sand",
        ),
        characters=("西蒙·克雷斯", "贾拉·沃"),
        actions=(
            "leans toward the glass habitat with possessive fascination",
            "presents the four sandking colonies with one warning hand raised",
        ),
        expressions=("greedy fascinated expression", "calm warning expression"),
        relationship_action="merchant warns collector beside the glass habitat",
    ),
    SandkingsPageBeat(
        turning_point="克雷斯把自己的全息头像赐给沙王并享受被崇拜",
        purpose="确立伪神主题与城堡人脸母题",
        shot="dramatic over-the-shoulder shot",
        narration="他给了它们自己的脸。很快，每一座城堡都学会了膜拜。",
        sfx="滋——",
        visual_tags=(
            "private alien manor laboratory",
            "giant holographic male face above glass habitat",
            "four sand castles carving the same human face",
            "tiny armies kneeling toward projected idol",
            "ominous orange glow",
        ),
        characters=("西蒙·克雷斯",),
        actions=("raises one hand beneath his own giant holographic face like a false god",),
        expressions=("arrogant delighted smile",),
    ),
    SandkingsPageBeat(
        turning_point="克雷斯用饥饿挑起四色战争，把杀戮变成派对节目",
        purpose="表现虐待升级并引入卡茜的反对",
        shot="high-angle party tableau",
        narration="饥饿让王国开战。宾客下注，克雷斯举杯，只有卡茜看见了这场游戏的恶心。",
        sfx="铿!",
        visual_tags=(
            "decadent science fiction cocktail party",
            "glass habitat battlefield at center",
            "black red white and orange insect armies colliding",
            "blurred wealthy guests around the arena",
            "burnt orange emergency lighting",
        ),
        characters=("西蒙·克雷斯", "卡茜·穆雷"),
        actions=(
            "raises a crystal glass while watching the tiny war with delight",
            "turns away from the battlefield in moral disgust",
        ),
        expressions=("cruel celebratory smile", "furious disgusted expression"),
        relationship_action="former lovers oppose each other across the cruel arena",
    ),
    SandkingsPageBeat(
        turning_point="沙蛛被围杀后，城堡上的克雷斯肖像变得恶毒",
        purpose="让沙王的学习与仇恨第一次可见",
        shot="extreme close-up with reflected portrait",
        narration="沙漠蜘蛛死在城门下。墙上的神像仍是克雷斯的脸，笑容却已经不是他的。",
        sfx="咔嚓",
        visual_tags=(
            "dead giant desert spider at miniature castle gate",
            "hundreds of sandkings surrounding prey",
            "malicious carved face in crumbling sand wall",
            "human eye reflected in glass",
            "psychological horror composition",
        ),
        characters=("西蒙·克雷斯",),
        actions=("touches the glass while staring at a cruel distorted version of his face",),
        expressions=("uneasy anger hidden beneath pride",),
    ),
    SandkingsPageBeat(
        turning_point="克雷斯刺穿白城堡，卡茜砸破生态缸并在混乱中倒下",
        purpose="把残酷游戏转成无法收回的灾难",
        shot="dynamic dutch angle",
        narration="标枪刺进白城堡，大锤砸碎生态缸。沙、泥和复仇一起涌进宅邸。",
        sfx="轰隆!",
        visual_tags=(
            "shattering giant glass habitat",
            "white sand castle pierced by spear",
            "avalanche of sand mud and alien insects",
            "woman falling behind shattered glass without gore",
            "violent diagonal composition",
        ),
        characters=("西蒙·克雷斯", "卡茜·穆雷"),
        actions=(
            "recoils while still gripping a long spear beside the broken habitat",
            "drops a heavy hammer while falling away from the burst tank",
        ),
        expressions=("sudden terrified shock", "shocked determined expression"),
        relationship_action="the broken habitat explodes between the two former lovers",
    ),
    SandkingsPageBeat(
        turning_point="沙王逃入宅邸，克雷斯掩盖死亡并用尸体喂养白沙母",
        purpose="表现主角人性彻底崩塌与白沙母壮大",
        shot="deep-focus cellar doorway shot",
        narration="酒窖里，白沙王拖走了死者。克雷斯没有报警，他选择继续喂养自己的神罚。",
        sfx="窸窣",
        visual_tags=(
            "dark futuristic wine cellar",
            "open cellar doorway shaped like a black mouth",
            "river of white insect creatures carrying a covered human form",
            "enormous unseen organism breathing beneath floor",
            "non-graphic horror",
        ),
        characters=("西蒙·克雷斯",),
        actions=("stands in the cellar doorway hiding a stained cutting tool behind his back",),
        expressions=("cold dissociated expression",),
    ),
    SandkingsPageBeat(
        turning_point="莉珊德拉带队进入地道，沙王用战术击倒火焰手",
        purpose="证明沙王已能组织超越昆虫本能的反击",
        shot="cinematic action wide shot",
        narration="火焰照亮地道。下一秒，地面像活物般下陷——猎人们才发现自己走进了战术。",
        sfx="轰!",
        visual_tags=(
            "industrial extermination team inside alien manor",
            "collapsing sand tunnel trap",
            "red sandking swarm climbing a yellow hazard suit",
            "laser beams and flamethrower light",
            "black and red colonies retreating tactically",
        ),
        characters=("西蒙·克雷斯", "莉珊德拉"),
        actions=(
            "points urgently toward the cellar while staying behind armored cover",
            "fires an industrial laser cannon into the collapsing insect tunnel",
        ),
        expressions=("panicked controlling expression", "focused battle expression"),
        relationship_action="cowardly employer directs the mercenary captain into danger",
    ),
    SandkingsPageBeat(
        turning_point="克雷斯背叛莉珊德拉，整座宅邸成为白沙王巢穴",
        purpose="完成主角主动杀人的堕落并展示失控规模",
        shot="long corridor confrontation",
        narration="黑城与红城倒下后，克雷斯把枪口转向了同伴。宅邸的每一面墙都在沙沙作响。",
        sfx="嗡——砰!",
        visual_tags=(
            "ruined retrofuturist manor corridor",
            "white insect swarms covering walls and ceiling",
            "laser cannon glowing between two figures",
            "breathing organic cracks in architecture",
            "non-graphic betrayal scene",
        ),
        characters=("西蒙·克雷斯", "莉珊德拉"),
        actions=(
            "aims a stolen industrial laser cannon while backing toward escape",
            "faces the weapon with one armored hand raised in disbelief",
        ),
        expressions=("frantic murderous fear", "furious betrayed shock"),
        relationship_action="terrified employer betrays the mercenary captain in the infested hall",
    ),
    SandkingsPageBeat(
        turning_point="克雷斯以朋友为饵逃跑，却发现沙王正蜕变为四臂人形",
        purpose="让逃生计划反转为进化揭示",
        shot="surreal horror close shot",
        narration="他把朋友留给巢穴，自己却打不开飞行器。脚边的甲壳裂开，伸出了四只小手。",
        sfx="喀啦",
        visual_tags=(
            "locked futuristic aircraft at night",
            "discarded sandking shells cracking open",
            "four tiny humanoid arms emerging from warm chrysalis",
            "distant party silhouettes trapped inside glowing manor",
            "burnt orange biological light",
        ),
        characters=("西蒙·克雷斯",),
        actions=("presses both hands against a locked aircraft hatch above a hatching chrysalis",),
        expressions=("desperate horrified expression",),
    ),
    SandkingsPageBeat(
        turning_point="贾拉·沃说明四臂新形态，克雷斯独自逃入荒野",
        purpose="给出真相并把终局引向错误方向",
        shot="split-depth hologram shot",
        narration=(
            "沃终于说出真相: 沙王不是宠物，它们只是在长大。"
            "克雷斯向东逃去，却早已分不清方向。"
        ),
        sfx="滋滋",
        visual_tags=(
            "alien wasteland outside ruined manor",
            "floating communication hologram",
            "diagram silhouette of two-legged four-armed creature",
            "dust storm erasing compass directions",
            "lonely fleeing figure",
        ),
        characters=("西蒙·克雷斯", "贾拉·沃"),
        actions=(
            "runs into the dust while looking back at the warning hologram",
            "appears as a calm hologram showing a four-armed evolutionary silhouette",
        ),
        expressions=("exhausted desperate fear", "grave warning expression"),
        relationship_action="merchant warns the fleeing collector through a failing hologram",
    ),
    SandkingsPageBeat(
        turning_point="长着克雷斯面孔的橙色四臂幼体抬走他",
        purpose="以自恋神像变成惩罚者完成闭环",
        shot="nightmare cinematic wide shot",
        narration="荒野尽头，橙色的孩子们找到了他们的神。每一张脸，都是克雷斯自己的脸。",
        sfx="呼——吸——",
        visual_tags=(
            "alien desert under black sky",
            "circle of small orange four-armed humanoid children",
            "every creature has the same pale angular male face",
            "captured man carried above them",
            "enormous breathing black doorway in distant dune",
            "final cosmic horror tableau",
        ),
        characters=("西蒙·克雷斯",),
        actions=(
            "struggles helplessly while four-armed orange children carry him toward darkness",
        ),
        expressions=("absolute despair",),
    ),
)


def extract_sandkings_source(source_path: Path) -> ExtractedSandkingsSource:
    resolved = source_path.expanduser().resolve()
    raw = resolved.read_bytes()
    text = raw.decode("utf-8", errors="strict")
    lines = text.splitlines(keepends=True)
    matches = [
        (index, int(match.group(1)))
        for index, line in enumerate(lines)
        if (match := _SECTION_PATTERN.fullmatch(line.rstrip("\r\n"))) is not None
    ]
    if [part for _, part in matches] != [1, 2, 3]:
        raise ValueError("source must contain exactly the three ordered Sandkings sections")

    third_start = matches[2][0]
    next_h2 = next(
        (
            index
            for index in range(third_start + 1, len(lines))
            if _H2_PATTERN.match(lines[index])
        ),
        len(lines),
    )
    extracted_parts: list[str] = []
    for position, (heading_index, _) in enumerate(matches):
        end_index = matches[position + 1][0] if position < 2 else next_h2
        extracted_parts.append("".join(lines[heading_index + 1 : end_index]).strip())
    extracted_text = "沙王\n\n" + "\n\n".join(extracted_parts) + "\n"
    _require_sandkings_story(extracted_text)
    return ExtractedSandkingsSource(
        source_path=resolved,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        extracted_sha256=hashlib.sha256(extracted_text.encode("utf-8")).hexdigest(),
        start_line=matches[0][0] + 1,
        end_line=next_h2,
        text=extracted_text,
    )


class SandkingsV5AcceptanceTextModel:
    """Deterministic authoring; only image generation is delegated to NovelAI V5."""

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
        _require_sandkings_story(request.chapter_text)
        if request.page_budget < PAGE_COUNT:
            raise ValueError(f"Sandkings acceptance requires at least {PAGE_COUNT} pages")
        scene_id = uuid4()
        page_by_beat: dict[str, int] = {}
        anchors_by_page: dict[int, list[str]] = {
            page_number: [] for page_number in range(1, PAGE_COUNT + 1)
        }
        total_beats = len(request.story_beats)
        for index, source_beat in enumerate(request.story_beats):
            page_number = min(PAGE_COUNT, (index * PAGE_COUNT) // total_beats + 1)
            page_by_beat[source_beat.beat_id] = page_number
            anchors_by_page[page_number].append(source_beat.anchor_id)
        fallback_anchor = request.story_beats[0].anchor_id
        document = StoryboardDocument(
            schema_version="1.1",
            storyboard_id=uuid4(),
            chapter_version=request.chapter_version,
            beat_resolutions=[
                BeatResolution(
                    beat_id=beat.beat_id,
                    status="represented",
                    page_numbers=[page_by_beat[beat.beat_id]],
                )
                for beat in request.story_beats
            ],
            scenes=[
                SceneCandidate(
                    scene_id=scene_id,
                    order=1,
                    title="沙王",
                    location="巴尔德尔星球的阿斯加德城、克雷斯庄园与荒野",
                    time_of_day="数月间，由人工光照至荒野黑夜",
                    summary="克雷斯把有智慧的沙王当作战争玩具，最终被成长后的造物反噬。",
                    beat_ids=[beat.beat_id for beat in request.story_beats],
                )
            ],
            pages=[
                PageCandidate(
                    page_id=uuid4(),
                    page_number=page_number,
                    page_type="splash" if page_number == 1 else "special",
                    turning_point=beat.turning_point,
                    scene_ids=[scene_id],
                    panels=[
                        PanelCandidate(
                            panel_id=uuid4(),
                            order=1,
                            purpose=beat.purpose,
                            shot=beat.shot,
                            characters=list(beat.characters),
                            dialogue=[],
                            narration=[beat.narration],
                            sfx=[beat.sfx],
                            visual_prompt=(
                                "mature retrofuturist science fiction horror manga, "
                                "black bone-white and burnt-orange limited palette, "
                                f"{', '.join(beat.visual_tags)}, no text"
                            ),
                            negative_prompt=(
                                "photorealistic, 3d render, cheerful comedy, text, watermark, "
                                "logo, speech bubble, panel border, low quality"
                            ),
                            source_anchor_ids=(
                                anchors_by_page[page_number] or [fallback_anchor]
                            ),
                        )
                    ],
                )
                for page_number, beat in enumerate(PAGE_BEATS, start=1)
            ],
        )
        return self._candidate(document, "sandkings-storyboard-12-pages-1.0")

    async def generate_bible_bundle(
        self,
        request: BibleDraftRequest,
    ) -> ModelCandidate[BibleDraftBundle]:
        self._require_profile()
        characters = [
            CharacterProfile(
                character_id=uuid4(),
                name=design.name,
                aliases=list(design.aliases),
                narrative_role=design.narrative_role,
                age_range=design.age_range,
                face_shape=design.face_shape,
                hair=design.hair,
                body_type=design.body_type,
                outfit=list(design.outfit),
                signature_features=list(design.signature_features),
                forbidden_changes=list(design.forbidden_changes),
                expression_range=list(design.expression_range),
                positive_prompt_fragment=", ".join(design.fixed_tags),
                negative_prompt_fragment=", ".join(design.negative_tags),
            )
            for design in CHARACTER_DESIGNS
        ]
        document = BibleDraftBundle(
            schema_version="1.0",
            character_bible=CharacterBibleDocument(
                schema_version="1.0",
                character_bible_id=request.character_bible_id,
                storyboard_version_id=request.storyboard_version_id,
                characters=characters,
                notes="《沙王》V5 验收，四名人类角色以固定服装、发型和标记保持连续。",
            ),
            style_bible=StyleBibleDocument(
                schema_version="1.0",
                style_bible_id=request.style_bible_id,
                storyboard_version_id=request.storyboard_version_id,
                summary="成熟复古未来主义科幻恐怖漫画，骨白与炭黑为主，焦橙色作为沙王威胁色",
                line_art="bold expressive ink line art with precise science fiction machinery",
                screentone="bone-white charcoal-black and burnt-orange limited color halftone",
                lighting="hard cinematic rim light with oppressive cellar darkness",
                background_density="detailed alien architecture with a single readable focal event",
                whitespace="strong silhouettes and clear caption-safe negative space",
                camera_language="one decisive cinematic graphic-novel image per page",
                positive_prompt_fragment=(
                    "very aesthetic, masterpiece, mature science fiction horror manga, "
                    "retrofuturism, bold ink line art, limited color palette, "
                    "bone white, charcoal black, burnt orange accent"
                ),
                negative_prompt_fragment=(
                    "worst quality, low quality, photorealistic, 3d render, cheerful comedy, "
                    "text, watermark, logo, speech bubble, panel border, bad anatomy"
                ),
                prohibited_elements=[
                    "照片感",
                    "三维渲染",
                    "喜剧气氛",
                    "水印",
                    "画面内文字",
                    "对白气泡",
                ],
            ),
        )
        return self._candidate(document, "sandkings-bibles-1.0")

    async def generate_character_tags(
        self,
        request: CharacterTagGenerationRequest,
    ) -> ModelCandidate[CharacterTagDraftBundle]:
        self._require_profile()
        designs = {design.name: design for design in CHARACTER_DESIGNS}
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
                    fixed_tags=list(designs[character.name].fixed_tags),
                    negative_tags=list(designs[character.name].negative_tags),
                    rationale=f"冻结{character.name}的年龄、发型、服装颜色与标志特征。",
                )
                for character in request.character_bible.characters
            ],
        )
        return self._candidate(document, "sandkings-character-tags-1.0")

    async def generate_prompt_bundle(
        self,
        request: PromptGenerationRequest,
    ) -> ModelCandidate[PromptDraftBundleDocument]:
        self._require_profile()
        character_by_name = {
            alias.casefold(): character.character_id
            for character in request.character_bible.characters
            for alias in (character.name, *character.aliases)
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
        packages: list[PanelPromptDraft] = []
        for page in request.storyboard.pages:
            beat = PAGE_BEATS[page.page_number - 1]
            for panel in page.panels:
                frame = frame_by_panel[str(panel.panel_id)]
                positions = cast(list[dict[str, Any]], frame["character_positions"])
                character_blocks: list[PromptCharacterBlockDraft] = []
                for order, character_name in enumerate(beat.characters):
                    character_id = character_by_name[character_name.casefold()]
                    character_blocks.append(
                        PromptCharacterBlockDraft(
                            character_id=character_id,
                            tag_set_id=tag_id_by_character[str(character_id)],
                            variable_tags=[beat.expressions[order]],
                            negative_tags=[
                                "extra limbs",
                                "bad hands",
                                "duplicate human character",
                                "inconsistent clothing",
                            ],
                            action=beat.actions[order],
                            order=order,
                            center=positions[order]["center"],
                        )
                    )
                packages.append(
                    PanelPromptDraft(
                        prompt_package_id=request.target_prompt_package_ids[str(panel.panel_id)],
                        panel_id=panel.panel_id,
                        base_visual_tags=[
                            "very aesthetic",
                            "masterpiece",
                            "mature science fiction horror manga",
                            "single full-page comic illustration",
                            *_human_count_tags(beat.characters),
                            beat.shot,
                            *beat.visual_tags,
                        ],
                        character_blocks=character_blocks,
                        relationship_action=beat.relationship_action,
                        style_tags=[
                            "retrofuturism",
                            "bold expressive ink line art",
                            "bone-white charcoal-black and burnt-orange limited palette",
                            "cinematic hard rim lighting",
                            "detailed alien architecture",
                        ],
                        negative_tags=[
                            "worst quality",
                            "low quality",
                            "photorealistic",
                            "3d render",
                            "cheerful comedy",
                            "text",
                            "watermark",
                            "logo",
                            "speech bubble",
                            "panel border",
                            "bad anatomy",
                            "cropped face",
                        ],
                        continuity_tags=[
                            "same character designs across pages",
                            "same black red white and orange sandking colonies",
                            "recurring cruel carved face motif",
                        ],
                    )
                )
        document = PromptDraftBundleDocument(
            schema_version="1.0",
            storyboard_version_id=request.storyboard_version_id,
            character_tag_bundle_version_id=request.character_tag_bundle_version_id,
            packages=packages,
        )
        return self._candidate(document, "sandkings-panel-plan-v5-1.0")

    def _require_profile(self) -> None:
        self.secret_reader(self.configuration.credential_profile_id)

    def _candidate(self, document: Any, template: str) -> ModelCandidate[Any]:
        return ModelCandidate(
            document=document,
            provider="manga-maker-local-deterministic",
            model=self.configuration.model,
            endpoint_host="local-acceptance",
            prompt_template_version=template,
            response_sha256="5" * 64,
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
            repair_attempts=0,
        )


def _require_sandkings_story(text: str) -> None:
    missing = [marker for marker in _REQUIRED_STORY_MARKERS if marker not in text]
    if missing:
        raise ValueError(f"Sandkings source is missing required markers: {', '.join(missing)}")


def _human_count_tags(characters: tuple[str, ...]) -> tuple[str, ...]:
    male_count = sum(name == "西蒙·克雷斯" for name in characters)
    female_count = len(characters) - male_count
    result: list[str] = []
    if male_count:
        result.append(f"{male_count}boy" if male_count == 1 else f"{male_count}boys")
    if female_count:
        result.append(f"{female_count}girl" if female_count == 1 else f"{female_count}girls")
    return tuple(result)


def character_design_by_id(
    characters: list[CharacterProfile],
) -> dict[UUID, CharacterDesign]:
    designs = {design.name: design for design in CHARACTER_DESIGNS}
    return {character.character_id: designs[character.name] for character in characters}
