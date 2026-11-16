# Satr Cut

Satr Cut is a production CLI for serialized vertical micro-dramas: 9:16, about one to two minutes an episode, a hook in the first three seconds, and a cliffhanger. It is not a canvas and it does not turn one idea into one MP4. Version 0.2.0.

The shot plan is the contract. `satrcut lint` rejects prompts that a cfg-1 video model will draw wrong. `satrcut render` checkpoints every attempt and resumes after the GPU host reboots. `satrcut qc` scores the take. `satrcut assemble` builds one checksummed 1080×1920 file.

The offline `mock` provider runs the whole path with no GPU and no API keys. ComfyUI (Wan2.2 I2V) is the first real video provider. Close-up lip-sync is a phase-2 interface and is off.

![Mock episode: hook, time card, yellow subtitle](docs/media/demo-hook.gif)

The moving bar and the shot id are the mock provider on purpose. A real Wan take replaces the picture. The yellow line, the name card, and the red time card are what assembly burns in.

| Hook | Time card | Burned-in line |
|---|---|---|
| ![Hook](docs/media/hook.png) | ![Time card](docs/media/timecard.png) | ![Subtitle](docs/media/subtitle.png) |

## Install

Python 3.11+, ffmpeg on `PATH`.

```bash
pip install -e ".[dev]"
satrcut --help
```

Optional later: `pip install -e ".[qc]"` for MediaPipe hands and faces, `pip install -e ".[asr]"` for faster-whisper. Without them the mock path still lints, renders, scores motion, and assembles. Identity (OpenCV SFace) runs only when `SATRCUT_YUNET` and `SATRCUT_SFACE` point at weight files you already have.

## One command path

```bash
satrcut init demo-hook
cd demo-hook
satrcut plan generate -e 1
satrcut lint -e 1
satrcut render -e 1 --provider mock
satrcut assemble -e 1
# demo-hook/deliverables/ep01.mp4   1080x1920
```

`examples/demo-hook` is that same project. It is a 10-cut rooftop hook (about 23 seconds), not a 90-second episode. The characters are placeholders.

Lint on that plan:

```text
22.5 s planned: 0 errors in 0 shots, 0 warnings
```

A second `satrcut render` skips every shot whose manifest is already PASS. A killed run leaves `partial.json` and retries that seed once.

## What the CLI does

| Command | What it guarantees |
|---|---|
| `satrcut init [dir]` | `series.yaml`, `episodes/ep01.yaml`, a local `.gitignore` |
| `satrcut plan generate -e 1` | Expands the beat sheet into `episodes/ep01.plan.json`, mock line audio, a music bed |
| `satrcut lint -e 1` | Shotlint. Exit 1 on any error. The GPU is not started |
| `satrcut keyframes brief -e 1` | A stills brief per shot |
| `satrcut keyframes check IMAGE --shot ID` | Aspect, and hands when MediaPipe is installed |
| `satrcut keyframes register IMAGE --shot ID --role start\|end` | sha256 in `keyframes/manifest.json` |
| `satrcut tts -e 1` | Pluggable TTS, then an ASR check per line. Mock by default. Mismatches are flagged, not rewritten |
| `satrcut render -e 1 --provider mock\|comfyui` | Resume-safe runner |
| `satrcut qc PATH --shot ID` | JSON report. PASS, TRIM, REVIEW, or FAIL |
| `satrcut assemble -e 1` | Freeze a sha256 manifest, burn subs, mix audio, write a thumbnail. Refuses on a hash mismatch |
| `satrcut status -e 1` | Per-shot attempt, seed, verdict |
| `satrcut service install` | systemd unit and a Windows Task Scheduler XML |
| `satrcut lipsync` | Tells you lip-sync is off |
| `satrcut models` | Public Wan filenames and checksums. Nothing is downloaded |
| `satrcut write -e 1` | Bible to a season arc, beats, and a plan that passes lint. Mock LLM by default |
| `satrcut registry show` | Sheets, wardrobe, identity method |
| `satrcut registry workflow -e 1 --shot 1-hook` | ComfyUI template with IP-Adapter or PuLID slots |
| `satrcut studio` | Local review page on 127.0.0.1: timeline, QC, approve / reject / retake, queue, GPU-hours |
| `satrcut dialogue route -e 1` | Move a line to listener / off-screen when lip-sync is not allowed |
| `satrcut assemble -e 1 --preset reelshort` | 1080×1920, -14 LUFS, safe-area subtitles. Also `tiktok`, `youtube_shorts`, `reels` |
| `satrcut render --provider comfyui --workflow ltx` | Same runner, LTX-Video or `wan22-5b` template. `fal` and `replicate` build a payload and do not POST |

OpenAI-compatible and Ollama writers are optional (`--provider openai` needs `SATRCUT_LLM_API_KEY`, `--provider ollama` needs a local daemon). The mock writer needs neither. fal and Replicate read `FAL_KEY` and `REPLICATE_API_TOKEN` only to decide that they are configured. v0.2 does not submit those jobs, so a key cannot spend credit. LatentSync and MuseTalk stay gated: under a mouth-sync score of 0.75 the line is routed off the face, and the model binary is not run.

```bash
satrcut write -e 1 --provider mock
satrcut lint -e 1
satrcut render -e 1 --provider mock
satrcut assemble -e 1 --preset reelshort
satrcut studio
```

Docker, with ffmpeg and Noto fonts:

```bash
docker build -t satrcut .
docker run --rm -v "$PWD":/work satrcut write -e 1 --provider mock
```

A Windows ComfyUI host can run `scripts/setup-comfy-windows.ps1 -ComfyRoot D:\ComfyUI\ComfyUI`. It installs the CLI and writes a start script. It does not download weights.

The docs site is the `docs/` tree, built with MkDocs (`mkdocs build`). GitHub Pages deploys it from the `docs` workflow on `main`. The package is PyPI-shaped (`python -m build`) and is not uploaded from this repo.

## Why this exists

Open canvases and "one idea, one video" agents already exist. A 30-episode series on a GPU box that reboots every 20–65 minutes needs a different tool:

1. A series bible and a shot plan, reused across episodes.
2. A linter that runs before any GPU time. At cfg 1.0 the negative prompt is ignored, so "no hands" draws hands.
3. A job runner that survives a reboot: finished attempts stay finished, the in-flight seed is retried once, ComfyUI is restarted, and a `STOP` file is honored and never deleted.
4. A motion QC gate at about 8 fps, with a tighter crop after a hand fail and pin→drift after a still.
5. One assemble command: 1080×1920, yellow centered subtitles, voice plus effects plus ducked music, sha256 manifest.

That is the gap. The rule text and the thresholds come from a scrubbed in-house pipeline in `seed/`. Story text from that series is not in this repo.

## Windows, with ComfyUI

ComfyUI is GPL-3.0. This project talks to it over HTTP and does not vendor it. You install ComfyUI and the Wan2.2 weights yourself. `satrcut models` prints the filenames and the public checksums.

```bat
py -3.11 -m pip install satrcut
cd %USERPROFILE%\projects
satrcut init demo-hook
cd demo-hook

set SATRCUT_PROJECT=%CD%
set SATRCUT_COMFY_URL=http://127.0.0.1:8188
set SATRCUT_COMFY_ROOT=D:\ComfyUI\ComfyUI

satrcut plan generate -e 1
satrcut lint -e 1
satrcut tts -e 1
satrcut keyframes brief -e 1

REM Optional: start the server yourself. If it is down and SATRCUT_COMFY_ROOT is set,
REM the runner starts it and retries the same seed once.
cd /d %SATRCUT_COMFY_ROOT%
python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch

cd /d %SATRCUT_PROJECT%
satrcut render -e 1 --provider comfyui --max-tries 4
satrcut status -e 1
satrcut qc renders\ep01\1-hook\a1_s611\take_s611.mp4 --shot 1-hook
satrcut assemble -e 1
```

Leave it running across reboots:

```bat
satrcut service install -e 1 --provider comfyui
schtasks /Create /TN "SatrCut Render" /XML "%CD%\deploy\satrcut-render.xml" /F
```

The same install writes `deploy/satrcut-render.service` for Linux:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/satrcut-render.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now satrcut-render.service
```

Create `.satrcut/STOP` to halt. The runner will not delete that file. `.satrcut/MAINT_WINDOW` is a scheduled GPU hand-off: if the file is created or updated after the runner starts, the next attempt waits. `SATRCUT_STOP_AT` is a unix timestamp. `SATRCUT_MAINT_UTC=07:30-11:35` is an optional daily window. It is empty unless you set it.

Replace `D:\ComfyUI\ComfyUI` with the portable directory that contains `main.py`.

## Environment

| Variable | Meaning |
|---|---|
| `SATRCUT_PROJECT` | Series root. Default: the nearest directory with `series.yaml` |
| `SATRCUT_COMFY_URL` | Default `http://127.0.0.1:8188` |
| `SATRCUT_COMFY_ROOT` | ComfyUI directory, used only to start the server when `/system_stats` fails |
| `SATRCUT_COMFY_PYTHON` | Python that can launch ComfyUI. Default `python` |
| `SATRCUT_STOP_AT` | Unix timestamp. Do not start an attempt that would pass it |
| `SATRCUT_MAINT_UTC` | Optional `HH:MM-HH:MM` daily window in UTC |
| `SATRCUT_MIN_FREE_GB` | Halt below this much free disk. Default 3 |
| `SATRCUT_QC_PY` | Reserved for a second interpreter that has MediaPipe |
| `SATRCUT_YUNET`, `SATRCUT_SFACE` | Optional OpenCV face weights. Identity is skipped without them |
| `SATRCUT_WHISPER_MODEL` | faster-whisper model name when `asr: whisper` |

Keys, if a later provider needs them, come only from the environment (`FAL_KEY`, `ELEVENLABS_API_KEY`, `HF_TOKEN`). The mock provider reads none. Plans and logs must not contain them.

## Docs

- [Plan format](docs/PLAN_FORMAT.md)
- [Write](docs/WRITE.md)
- [Providers](docs/PROVIDERS.md)
- [Studio](docs/STUDIO.md)
- [Export and subtitles](docs/EXPORT.md), including the lip-sync stub
- [Failure taxonomy](docs/FAILURE_TAXONOMY.md)
- [Why shots fail](docs/ROOT_CAUSES.md)
- [Style rules](docs/STYLE_RULES.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Contributing](CONTRIBUTING.md)

## Limits

The in-house gate, on a held-out set, caught about 45% of bad takes. It still misses a melted hand at the frame edge, a hand on a wheel, a quiet mouth, and a frozen face with only a window moving. A person still watches every take. `REVIEW` means a deliberate hand insert: do not ship it until someone has looked.

On-screen lip-sync failed 0 of 12 close-ups in that pipeline. Lines go on the listener, off-screen, or the back of a head. `line_mode: lipsync_cu` is an error unless the plan sets `lipsync_approved`, and even then nothing in v0.1 applies a lip-sync model.

## License

MIT. See [LICENSE](LICENSE).
