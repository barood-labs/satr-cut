"""House style for a vertical micro-drama.

Thresholds and prompt boilerplate come from seed/v3/style.py. Prompts describe
what is in frame. Wan2.2 A14B with the lightx2v 4-step LoRA runs at cfg 1.0,
so a negative prompt is not a substitute for lint.
"""

from __future__ import annotations

BOIL_V5 = (
    "Photoreal cinematic live action, glossy commercial-grade look, cinematic rim lighting separating the subject from "
    "the dark background, bright catchlights in the eyes, sharp focus, 85mm lens, shallow depth of field with bokeh, "
    "subtle 35mm film grain."
)
BOIL_INSERT = (
    "Photoreal cinematic live action, glossy commercial-grade look, rim light on the edges, 85mm macro lens, "
    "shallow depth of field with bokeh, subtle 35mm film grain."
)
BOIL_AERIAL = (
    "Photoreal cinematic aerial drone footage, glossy commercial-grade look, rim light along wet rooftops and steel edges, "
    "night city lights, subtle 35mm film grain."
)
GRADE_WORDS = {
    "desire": "warm amber candle-like light, golden glow on skin",
    "danger": "cool blue-grey night light, steel-blue shadows",
    "grief": "cool blue-grey light, desaturated, soft cold glow",
    "flashback": "warm golden flashback light, soft bloom",
    "neutral": "",
}
GRADE_FX = {
    "desire": "colorbalance=rs=0.10:gs=0.02:bs=-0.10:rm=0.08:bm=-0.08:rh=0.05:bh=-0.06,eq=saturation=1.12:contrast=1.05",
    "flashback": "colorbalance=rs=0.08:gs=0.04:bs=-0.10:rm=0.06:bm=-0.06,eq=saturation=1.05:brightness=0.02,gblur=sigma=0.4",
    "danger": "colorbalance=rs=-0.08:bs=0.10:rm=-0.05:bm=0.08,eq=saturation=0.85:contrast=1.08",
    "grief": "colorbalance=rs=-0.06:bs=0.08:rm=-0.04:bm=0.06,eq=saturation=0.75:contrast=1.04",
    "neutral": "eq=contrast=1.03",
}
CAMERA_V5 = "The camera creeps in slowly and steadily the whole time."
CAMERA_DRIFT = "The camera drifts slowly forward with a gentle handheld float."
TAIL_V5 = "Same people, wardrobe and place as the start image, moving continuously into the end image. One continuous shot."
NEG_V5 = (
    "static, motionless, frozen video, still frame, locked-off camera, crossfade, dissolve, double exposure, morphing face, "
    "two faces merging, fused lips, different face, deformed face, melting, extra person, extra limbs, extra fingers, "
    "deformed hands, hands on face, text, subtitles, logo, watermark, cut, scene change, talking, mouth moving, flat lighting, "
    "dull colours, low contrast"
)
NEG_V5_NOHANDS = NEG_V5 + ", hands, fingers"
NEG_KISS = NEG_V5 + ", nudity, open-mouth kiss, tongue, faces overlapping, three eyes, merged noses"
EDIT = {
    "max_cut_s": 3.0,
    "kiss_cut_s": (3.0, 7.0),
    "seduce_cut_s": (3.5, 5.5),
    "reaction_s": (0.8, 1.4),
    "min_cu_ratio": 0.70,
    "push_in_scale": (1.00, 1.05),
    "slowmo_approach": 0.87,
    "hook_s": (2.5, 3.5),
    "stakes_by_s": 30.0,
    "endings": ("uncompleted_action", "demand", "entrance", "confession", "surrender", "interruption"),
    "subtitle": {"font": "DejaVu Sans", "size": 64, "colour": "&H0000FFFF", "outline": 4, "align": 2, "margin_v": 420},
}


def prompt(kind: str, camera: str, action: str, grade: str = "neutral", extra: str = "") -> str:
    boil = {"insert": BOIL_INSERT, "aerial": BOIL_AERIAL}.get(kind, BOIL_V5)
    grade_words = GRADE_WORDS.get(grade, "")
    grade_sentence = (grade_words[:1].upper() + grade_words[1:] + ".") if grade_words else ""
    return " ".join(part for part in (boil, camera, action, grade_sentence, extra, TAIL_V5) if part)


def negative(kind: str, *, kiss: bool = False) -> str:
    if kiss:
        return NEG_KISS
    if kind in ("insert", "aerial"):
        return NEG_V5_NOHANDS
    return NEG_V5
