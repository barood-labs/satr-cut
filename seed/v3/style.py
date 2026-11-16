"""DramaBox house style. Imported by build_v5_plan.py and
every later episode builder (ep2-30) so prompts, negatives, grades and edit rules are the same everywhere.
Rules baked in: glossy look, rim light, catchlights, 85mm, slow push-in/drift on every shot, warm amber ONLY for desire,
cool blue-grey for danger/grief, kisses allowed (profile/OTS ECU, lips centred, 3-7 s, hands on neck/waist/shoulders,
then a reaction), seductive beats ~4.5 s, approach shots 10-15 % slow-mo with breath+heartbeat, hook -> red cursive time
card -> loop back, end on an interruption / confession word."""
BOIL_V5 = ("Photoreal cinematic live action, glossy commercial-grade look, cinematic rim lighting separating the subject from "
           "the dark background, bright catchlights in the eyes, sharp focus, 85mm lens, shallow depth of field with bokeh, "
           "subtle 35mm film grain.")
BOIL_INSERT = ("Photoreal cinematic live action, glossy commercial-grade look, rim light on the edges, 85mm macro lens, "
               "shallow depth of field with bokeh, subtle 35mm film grain.")
BOIL_AERIAL = "Photoreal cinematic aerial drone footage, glossy commercial-grade look, rim light along wet rooftops and steel edges, night city lights, subtle 35mm film grain."
GRADE_WORDS = {
    "desire": "warm amber candle-like light, golden glow on skin",
    "danger": "cool blue-grey night light, steel-blue shadows",
    "grief": "cool blue-grey light, desaturated, soft cold glow",
    "flashback": "warm golden flashback light, soft bloom",
    "neutral": "",
}
# ffmpeg grade per colour code (applied in assembly on every cut, so v4 takes match the code too)
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
NEG_V5 = ("static, motionless, frozen video, still frame, locked-off camera, crossfade, dissolve, double exposure, morphing face, "
          "two faces merging, fused lips, different face, deformed face, melting, extra person, extra limbs, extra fingers, "
          "deformed hands, hands on face, text, subtitles, logo, watermark, cut, scene change, talking, mouth moving, flat lighting, "
          "dull colours, low contrast")
NEG_V5_NOHANDS = NEG_V5 + ", hands, fingers"
NEG_KISS = NEG_V5 + ", nudity, open-mouth kiss, tongue, faces overlapping, three eyes, merged noses"
WARDROBE = {   # example lock for the demo series (change only with an on-screen reason)
    "Alex": "the man with short dark hair in a black wool coat, open collar, no jewelry in frame",
    "Jordan": "the woman with shoulder-length dark hair in a camel coat and a gold hoop in the left ear",
    "Sam": "the colleague with a sharp bob in a slate blazer over a cream blouse",
}
EDIT = {
    "max_cut_s": 3.0, "kiss_cut_s": (3.0, 7.0), "seduce_cut_s": (3.5, 5.5), "reaction_s": (0.8, 1.4),
    "min_cu_ratio": 0.70, "push_in_scale": (1.00, 1.05), "slowmo_approach": 0.87, "hook_s": (2.5, 3.5), "stakes_by_s": 30.0,
    "endings": ("uncompleted_action", "demand", "entrance", "confession", "surrender", "interruption"),
    "subtitle": {"font": "DejaVu Sans", "size": 46, "colour": "&H0000FFFF", "outline": 4, "align": 2, "margin_v": 330},
    "name_card": {"font": "Cinzel", "size": 64, "sub_font": "Playfair Display", "sub_size": 30},
    "time_card": {"font": "Alex Brush", "size": 150, "colour": "&H002020E0"},
}
def prompt(kind, camera, action, grade="neutral", extra=""):
    boil = {"insert": BOIL_INSERT, "aerial": BOIL_AERIAL}.get(kind, BOIL_V5)
    g = GRADE_WORDS.get(grade, "")
    return " ".join(x for x in (boil, camera, action, (g[0].upper() + g[1:] + ".") if g else "", extra, TAIL_V5) if x)
