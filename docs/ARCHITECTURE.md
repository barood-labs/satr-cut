# Architecture

```text
series.yaml + episodes/epNN.yaml
        |
        v
  satrcut plan generate          style.py builds the positive prompt
        |
        v
  satrcut lint                   L01–L25, K01–K02. Exit 1 stops the run
        |
        v
  keyframes                      start + end, sha256 manifest
        |
        v
  satrcut render                 provider: mock | comfyui
        |                        partial.json while in flight, manifest.json when done
        v
  satrcut qc                     PASS | TRIM | REVIEW | FAIL
        |                        HAND -> tighter crop, STILL -> pin to drift
        v
  satrcut assemble               frozen sha256 manifest -> 1080x1920 mp4 + srt + jpg
```

## Packages

| Module | Role |
|---|---|
| `project` | Load `series.yaml`. Find the project from `SATRCUT_PROJECT` or the nearest `series.yaml` |
| `plan` | Expand the episode yaml. The JSON is the contract |
| `lint` | The shotlint rules. Regexes are tested against `seed/v2/shotlint.py` |
| `style` | Prompt boilerplate, grade filters, edit thresholds |
| `keyframes` | Placeholder stills, briefs, crop, drift, register |
| `speech` | TTS then ASR for every line. Noise burst and music bed |
| `runner` | The crash-proof loop |
| `store` | SQLite index. The JSON files are the source of truth; the database is rebuilt from them |
| `qc` | 8 fps motion gate, optional MediaPipe, optional SFace |
| `assemble` | Freeze, verify, ffmpeg, yellow subs, ducked mix, thumbnail |
| `providers` | `mock`, `comfyui` (Wan2.2 A14B over HTTP) |
| `lipsync` | Phase-2 stub. `apply` always refuses |
| `service` | systemd unit and Windows Task Scheduler XML |
| `cli` | `satrcut` |

`seed/` is the scrubbed pipeline these modules were refactored from. The package does not import it at runtime. Tests do, to prove the hand and negation patterns have not drifted.

## Resume

Before every attempt the runner writes `partial.json` with `state: in_flight` and the seed, using a temp file and `os.replace`. Then it renders.

- A shot with `manifest.json` in PASS, TRIM, REVIEW, or FAIL_ALL is skipped. `--retry-failed` drops FAIL_ALL only.
- An attempt that already has a verdict in `partial.json` is not rendered again. Its seed is unchanged.
- An `in_flight` attempt with `resumes: 0` is retried with the same seed. The counter becomes 1. A second death records `RENDER_ERROR` and the ladder advances.
- `InfraError` (ComfyUI health) restarts the server and retries the same seed inside that attempt, then advances if it fails again.

`STOP` and `MAINT_WINDOW` live in `.satrcut/`. The runner never deletes them.

SQLite (`jobs.sqlite`, WAL) is updated after each JSON write and rebuilt from manifests by `satrcut status`. Losing the database does not lose a finished take.

## Assemble order

1. Freeze sha256 of the plan and of each accepted take.
2. Refuse if a hash, a path, or the cut order does not match.
3. Per cut: trim, fps, scale to delivery size, digital push-in, grade (warm only for desire and flashback).
4. Concat.
5. Mix dialogue and effects. Split the voice bus, sidechain-compress the music bed, loudnorm. If loudnorm fails, the mix is written without it.
6. Burn ASS subtitles (yellow, black outline, centered) plus the red time card and the first-appearance name.
7. Write SRT, a thumbnail at the stakes line, and `epNN.report.json`.

Phone UI and any other readable text belong in that burn-in, not in the video model.

## What is intentionally missing

- A web UI.
- A cloud video provider and a cost ledger.
- Kokoro or ElevenLabs (the TTS interface is the place they will go).
- Working close-up lip-sync.
- LoRA training. A registry can warn via L13; nothing trains.
- Cache eviction (`satrcut cache gc` is phase 2).
