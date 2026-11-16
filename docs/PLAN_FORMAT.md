# Plan format

Two files describe a series. `satrcut plan generate` turns them into one JSON document that lint, the runner, QC, and assemble all read.

```text
series.yaml                 bible: cast, places, wardrobe, providers, sizes
episodes/ep01.yaml          beat sheet: scenes and shots
episodes/ep01.plan.json     generated contract (do not hand-edit unless you know why)
```

`satrcut init` writes the first two from the demo. `examples/demo-hook` is the same pair.

## series.yaml

```yaml
name: demo-hook
aspect: "9:16"
render: { width: 480, height: 848, fps: 16 }     # Wan-friendly
delivery: { width: 1080, height: 1920, fps: 24 }
target_duration_s: 23          # 80–100 for a full episode; the demo is a short hook
style: dramabox
hook_s: 3
endings: [uncompleted_action, demand, entrance, confession, interruption]
forbidden_names: []            # empty unless this series wants a name ban
characters:
  alex:
    display: Alex
    look: "the man with short dark hair in a black wool coat, open collar"
    unique_detail: "a thin scar at the left eyebrow"   # face, neck, collar — not a ring or a watch
    voice: mock:alex
    sheet: assets/alex_sheet.png
places:
  rooftop: { desc: "night rooftop, wet concrete, unreadable neon bokeh" }
wardrobe:
  rooftop:
    alex: "black wool coat, open collar"
    jordan: "camel coat, gold hoop earring"
providers:
  video: mock          # or comfyui
  tts: mock
  asr: mock            # or whisper, if faster-whisper is installed
music: audio/bed.wav
```

`look` and `unique_detail` are copied into prompts and keyframe briefs. Do not put "no hands", "without a watch", or any other negation in them. At cfg 1.0 the model draws the noun.

Wardrobe is locked per place. A change between scenes is a warning (`L10 WARDROBE_CHANGE`) until the scene sets `wardrobe_change_shown: true` after a shot that shows the change.

`forbidden_names` is per project. It is empty by default. The tool does not ship a built-in name ban.

## episodes/ep01.yaml

```yaml
episode: 1
title: Rooftop
ending: uncompleted_action     # uncompleted_action | demand | entrance | confession | surrender | interruption
time_card: "Ten Minutes Earlier"
stakes_line: "Don't answer that."
card_duration_s: 1.2
seeds: [611, 733, 857, 919]
scenes:
  - id: "EXT. ROOFTOP - NIGHT"
    place: rooftop
  - id: "INT. STAIRWELL - NIGHT"
    place: stairwell
shots:
  - id: 3-os
    scene: "EXT. ROOFTOP - NIGHT"
    place: rooftop
    shot_type: cu              # ecu | cu | ots | two_shot_wide | insert | establishing
    kind: character            # character | insert | aerial
    frames: 41                 # 4n+1, and <= 81 for Wan A14B
    in_frame: [jordan]
    faces_visible: [jordan]    # the speaker of an O.S. line must not be in this list
    grade: danger              # desire and flashback are warm; danger and grief are cool
    anchor: keyed              # pin | drift | keyed
    line_mode: listener        # os | listener | vo | insert | lipsync_cu
    speaker: "Alex (O.S.)"
    line: "Don't answer that."
    subtitle: "Don't answer that."
    camera: "Vertical close-up of the listener. The camera creeps in slowly."
    action: "Jordan listens, eyes widening, lips sealed, the neon behind her staying still."
    motion: "eyes widen; lips sealed; camera creeps in"
```

`camera` needs a move: creep, drift, dolly, handheld. Do not write "push" — the hand rule treats that word as a hand. The cut still gets a digital push-in at assemble time (`push: [1.0, 1.05]`).

`action` is one thing the viewer can see. Say lips sealed or jaw set on a listener. Do not name hands, fingers, wheels, wipers, doors that open, distant vehicles, or readable text.

`frames / fps` should land between 1.5 and 3.0 seconds. The first cut is the hook and must be 2.5–3.5 seconds, and also at or under 3.0 so it satisfies the max-cut rule. A kiss, if you stage one, may run 3–7 seconds.

The first shot should have `beat: hook`. Generate inserts a time card after it when `time_card` is set. The last shot should set `end_on: line_end`.

About 70% of picture cuts (everything except the time card) should be `cu` or `ecu`.

## Generated plan

`episodes/ep01.plan.json` adds, for every shot:

- `prompt` and `negative` from `satrcut.style`
- `start_frame` and `end_frame` paths under `keyframes/`
- `line_audio` under `audio/lines/`
- `qc` spec for the gate (faces expected, mouth closed, 0.45 face-width gap, 8 fps)
- `duration_s`, width, height, seeds
- a `cuts` list with `at`, `dur`, `framing`, `grade`, `push`, lines, and optional `sfx`

Cuts reference takes as `render:<shot id>`. The time card is `card:time`.

`stakes_at_s` is the absolute time of `stakes_line`. It must be at or before 30 seconds.

## Checks the file itself must pass

`satrcut lint` exits 1 if any of these are errors. Warnings print and do not block.

| Id | Level | Fails when |
|---|---|---|
| L01 | error | The positive prompt names a hand, finger, ring, watch, wheel, mug, phone-at-ear, or similar. `allow_hands` with a reason downgrades to a warning and QC returns REVIEW |
| L02 | error | A negation (`no`, `not`, `without`, `out of frame`, …) sits in the positive prompt |
| L03 | error | Two faces in a close frame, or contact language (nose-to-nose, kiss, lean in) without a staged kiss block |
| L04 | error | A line has no `line_mode`, uses `lipsync_cu` while lip-sync is off, or the speaker's face is visible |
| L05 | error | A door opens, someone enters, or a door has no end frame and no anchor |
| L06 | error | A distant or moving vehicle, or wipers |
| L07 | warn | Signs, labels, logos, "screen shows" — burn text in assembly |
| L08 | warn | A photo prop with no `photo_faces` |
| L09 | error | An A14B shot has no start frame, or no end frame unless `anchor` is `pin` |
| L10 | error / warn | A character is in frame with no scene wardrobe, or the wardrobe string changes across scenes without `wardrobe_change_shown` |
| L11 | error | A character sheet's look or unique detail names a hand or jewelry |
| L12 | error | `frames` is not 4n+1, or longer than 81 |
| L13 | warn | An approved LoRA exists and this shot does not load it |
| L14 | error | `anchor: pin` plus a camera move |
| L16 | error | A picture cut is longer than 3 s (kisses exempt) |
| L17 | error | CU/ECU is under 70% of picture cuts |
| L18 | error | No camera move in the prompt, or a picture cut has no push-in |
| L19 | warn | A lateral walk across frame |
| L20 | error | Ending type is missing or not in the allowed set |
| L21 | error | Cut 1 is not a 2.5–3.5 s hook, or the stakes line is after 30 s |
| L22 | error | Prompt is missing rim, glossy, or (except aerials) 85mm |
| L23 | warn | Warm grade outside a hook, desire, or flashback beat |
| L24 | error | An off-screen line plays over the speaker's face |
| L25 | error | A `forbidden_names` entry appears. The default list is empty |
| K01, K02 | error | A kiss block is missing angle, soft face, or keyframes, or the cut length is wrong |

A staged kiss is a shot field:

```yaml
kiss: { angle: profile, soft_face: jordan, beat: kiss }
```

Unstaged contact stays an error.

## Job files the runner writes

```text
renders/ep01/job.json
renders/ep01/gate_log.jsonl
renders/ep01/status.json
renders/ep01/<shot>/partial.json      # only while an attempt is in flight
renders/ep01/<shot>/manifest.json     # when the shot is done (PASS, TRIM, REVIEW, or FAIL_ALL)
renders/ep01/<shot>/a1_s611/take_s611.mp4
renders/ep01/<shot>/a1_s611/qc.json
.satrcut/jobs.sqlite                  # index, rebuilt from the JSON
```

`manifest.json` is the checkpoint. A finished attempt is never rendered again. `partial.json` means the process died mid-attempt; the next start retries that seed once, then moves to the next seed.

Assemble freezes `deliverables/ep01.manifest.json` with the sha256 of the plan and of every accepted take. `satrcut assemble --no-freeze` rebuilds from that file and exits non-zero if any hash differs.
