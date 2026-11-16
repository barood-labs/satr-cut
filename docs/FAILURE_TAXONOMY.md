# Failure taxonomy

Ranked by what a vertical-drama run actually dies from. The counts and the craft notes are in [ROOT_CAUSES.md](ROOT_CAUSES.md). This page is the code's vocabulary: what the CLI prints, and what it does next.

Do not spend GPU time on a plan that still has lint errors.

## Lint (before the GPU)

Exit code 1. No render is started.

| Rule | You will see | What to do |
|---|---|---|
| L01 HANDS | prompt names hands, fingers, a ring, a watch, a wheel, a mug | Frame from the collarbones up. Describe eyes, breath, light. A deliberate insert sets `allow_hands` and comes back from QC as REVIEW |
| L02 NEGATION | `no`, `not`, `without`, `out of frame` in the positive prompt | Say what is in frame. The 4-step model runs at cfg 1.0 and ignores the negative prompt |
| L03 TWO_FACES / CONTACT | two faces in a close shot, or nose-to-nose language | One face, or `ots` / `two_shot_wide` with a gap of at least 0.45 face widths. A kiss needs a `kiss` block |
| L04 | missing `line_mode`, `lipsync_cu`, or the speaker is visible | Listener, O.S., or back of head. Attach `line_audio` |
| L05 | a door opens or a stranger appears | Closed door, sound, then the next shot. Give the door an end frame |
| L06 | a distant vehicle or wipers | Interior, pinned, plus sound. Park the wipers |
| L07 | signs, labels, "screen shows" | Warning. Burn the glyphs in assembly |
| L09 | no start or end keyframe | Register both. `anchor: pin` may omit the end frame because it is the start frame |
| L10 | wardrobe not locked, or a change that is never shown | Put the character on the scene lock. Show the change, then set `wardrobe_change_shown` |
| L11 | the character sheet itself names a hand or a ring | Move the unique detail to the face, ear, or collar |
| L12 | frame count is not 4n+1, or the shot is over 81 frames | Fix the count. Split the beat |
| L14 | pin anchor plus a camera move | Use `drift` or `keyed`, or lock the camera off |
| L16 | a cut is over 3 seconds | Split it. Kisses may be 3–7 s |
| L17 | under 70% close-ups | More ECU / CU, fewer wides |
| L18 | no camera move, or no push-in on the cut | Creep or drift in the prompt. Generate adds the push |
| L20 | ending type is not allowed | `uncompleted_action`, `demand`, `entrance`, `confession`, `surrender`, `interruption` |
| L21 | the hook is the wrong length, or the stakes line is late | Cut 1 is 2.5–3.5 s and also ≤ 3 s. Stakes by 30 s |
| L22 | missing rim, glossy, 85mm, or catchlights | `style.prompt` adds these. A hand-written prompt must too |
| L24 | the speaker's face is on screen for an off-screen line | Same fix as L04, on the cut |
| L25 | a name on this project's `forbidden_names` | The default list is empty |
| K01 / K02 | a kiss is unstaged or the wrong length | Profile or OTS, one face soft, both keyframes, then a reaction cut |

`L15` is a warning. A full episode (`target_duration_s` ≥ 80) should land between 80 and 100 seconds and between 35 and 45 cuts. A short demo is checked against its own target (±12%) instead.

## Render

| Status | Exit | Meaning | Next step |
|---|---|---|---|
| PASS / TRIM / REVIEW | 0 for the shot | QC accepted the take. REVIEW still needs a person before assemble, unless `--allow-human-accept` | Manifest is written. A later run skips the shot |
| FAIL | continues | QC rejected the take | HAND: crop start and end 12% tighter, at most twice. STILL or KEN_BURNS while `anchor` is `pin`: swap to drift. Otherwise the next seed |
| RENDER_ERROR | continues | The provider raised | Next seed. An `InfraError` (ComfyUI down) restarts the server and retries the same seed once first |
| in_flight in `partial.json` | process is gone | The host died after the checkpoint and before the manifest | Next start retries that seed once, then moves on. The finished shots are not rendered again |
| FAIL_ALL | 1 | Every attempt failed | Manifest records it so the loop does not spin. `satrcut render --retry-failed` tries again |
| HALT | 3 | `STOP`, `MAINT_WINDOW`, `SATRCUT_STOP_AT`, `SATRCUT_MAINT_UTC`, or low disk | The STOP file is not deleted. The service waits and resumes |

Disk floor is `SATRCUT_MIN_FREE_GB` (default 3) on the project volume, not a hardcoded drive letter.

## QC

Sample rate is about 8 fps. Verdicts:

| Verdict | Meaning |
|---|---|
| PASS | No persistent defect |
| TRIM | Frame-level defects exist, and a clean window is long enough. Assemble uses that window |
| REVIEW | The shot allowed hands. A person looks before it ships |
| FAIL | Re-render |

Reasons the runner acts on:

| Reason | Detector | Auto-retake |
|---|---|---|
| HAND, HAND_ANATOMY | MediaPipe hands, when installed | Crop 12% tighter, twice max |
| FACES_CLOSE | Two boxes closer than 0.45 face widths | New seed |
| FACE_LOST, EXTRA_FACE | Face count drifted | New seed |
| MOUTH_OPEN, MOUTHING | Listener mouth moves | New seed |
| IDENTITY_LOW, IDENTITY_DRIFT | SFace cosine, only when the onnx weights are present | New seed |
| STILL | Almost no frame-to-frame change | pin → drift |
| KEN_BURNS | Later frames are a zoom or crop of frame 0 | pin → drift |
| DISSOLVE, SOFT, NOISY, CUT | Mid-frame is a blend, a blur, noise, or a hard cut (the v3 sanity checks, when that path is on) | New seed |

Without MediaPipe the report lists `hands`, `faces`, and `mouth` under `skipped` and still fails a frozen or Ken Burns clip. Without SFace weights, `identity` is skipped rather than failed. That is deliberate: a missing model is not a melted face.

Honest limit: on the held-out set this family of checks caught about 45% of bad takes. A person still watches every take once.

`SATRCUT_QC_RESPECT_SIDECAR=1` makes QC honor a `force_verdict` field in a sibling `.qc.json`. That switch is for tests of the retake ladder. Leave it unset in production.

## Assemble

Exit 1, and no mp4 is replaced, when:

| Check | Message |
|---|---|
| HASH_MISMATCH | A take's sha256 or the plan's sha256 differs from the frozen manifest |
| MISSING_TAKE | No manifest, or the status is not PASS or TRIM |
| SPEAKER_VISIBLE | A line's speaker is in `faces_visible` |
| NO_AUDIO | A subtitle or line has no wav |

`--allow-human-accept` also allows REVIEW. `--allow-fail` also allows FAIL_ALL. Neither is the default. Placeholders are not generated.

## Lip-sync

`satrcut lipsync` exits 2. There is no model call. Treat a request for on-screen mouth sync as an L04 problem, not as a provider outage.
