# Why vertical short-drama shots fail (ranked)

Case study from a 90-second, ~44-cut DramaBox-style episode. Source: a full QC log
plus per-take state files from one in-house production (78 rejected takes or
versions, plus 8 GPU-host reboots or crashes). One take can have more than one
cause, so the counts add up to more than 78.

What reviewers saw (symptoms): bad hands 32, lip-sync 12, frozen/still 9, mouth
moving with no sound 7, edit errors 7, faces merging 7, vehicles 6, wardrobe 5,
garbage text 5, crashes 4, wrong action 3, identity 2, a person appearing behind
a door 2.

| # | Root cause | Takes it explains | Fix in this tool |
|---|---|---|---|
| 1 | **The prompts asked for hands.** Actions and even character sheets put hands, rings, watches, wheels, mugs or phones in every prompt. The video model draws them, then melts them. Emotion words ("breaks down", "sobs") also summon "emotional hands". | 22 (hand words) + 8 (emotion → hands) + 7 (hands already in the start frame) | `shotlint` L01 HANDS, L11 HAND_DETAIL; keyframes framed collarbones-up; runner crops 12% tighter after a HAND fail |
| 2 | **"No hands" / "no wheel" did nothing.** The lightx2v 4-step Wan2.2 A14B setup runs at cfg 1.0, where the negative prompt is ignored, and a negated noun in the positive prompt gets drawn. | 13 | `shotlint` L02 NEGATION: say what IS in frame, never name the thing you do not want |
| 3 | **The check was too weak.** Takes passed a 2-fps frame sheet and only failed later in a full-motion review (hands late in the shot, mouthing, Ken Burns stills). | 18 | `qc_gate.py`: per-take automatic gate at 8 fps (MediaPipe hands/face, face distance, identity, mouth, real-motion + Ken Burns detector) |
| 4 | **On-screen lip-sync never worked.** MuseTalk / LatentSync / InfiniteTalk close-ups: 0 of 12 passed. | 14 | Lines go only on listener / O.S. / back-of-head shots. `shotlint` L04 bans `lipsync_cu` unless a working close-up is explicitly approved |
| 5 | **GPU-host crashes and reboots** killed renders (in this production, the box rebooted every 20–65 minutes) and training. | 12 | Crash-proof runner: restart the ComfyUI server and retry the same seed once; every shot is resumable from its manifest; never re-run a finished attempt |
| 6 | **Edit mistakes**: assembling from a stale shot file, lost O.S. audio, a line playing over the wrong face. | 10 | Assembler uses a frozen sha256 manifest for every take and the shot list; build refuses on any mismatch; check pass for lines, subtitles and mouths |
| 7 | **Close two-shots merge faces.** Lean-in, forehead touch, nose-to-nose framing: faces melt into each other. | 10 | `shotlint` L03 CONTACT / TWO_FACES; gate FACES_CLOSE (min gap 0.45 face width) |
| 8 | **Distant vehicles and wipers** warp, slide or bend. | 7 | `shotlint` L06 VEHICLE_MOTION / WIPERS |
| 9 | **No end frame / bad anchor**: without an end keyframe the action drifts off plan; pinned start=end with no motion plan comes out frozen. | 5 + 4 (still method) + 2 (over-pinned) | `keyframes.py` locked first+last frames; `shotlint` L09 / L14; runner swaps pin → drift after a STILL fail |
| 10 | **Generated text** on phones, signs, captions. | 4 | `shotlint` L07 TEXT |
| 11 | **Wardrobe continuity was never planned** (a costume change that was never shown). | 3 | Fixed wardrobe per scene; `shotlint` L10 WARDROBE / WARDROBE_CHANGE |
| 12 | **Weak or unused character LoRAs.** Identity then rests on keyframes alone. | 2 (+ drift risk on every face) | LoRA registry; lint warns when an approved LoRA is not loaded |
| 13 | Prop held to the face (phone at ear). | 1 | L01 |

## The short version

About 6 in 10 failures come from three things: **prompts that ask for hands**,
**negatives that the 4-step model ignores**, and **a check that looked at stills
instead of motion**. Next come **on-screen lip-sync that never worked**, **close
two-shots**, and **GPU-host crashes**. Everything else is edit-process slips that
a checksum manifest stops.

Do not spend GPU time on a plan that still has lint errors.
