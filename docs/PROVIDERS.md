# Providers

Three slots, all selected from `series.yaml`:

```yaml
providers:
  video: mock     # mock | comfyui
  tts: mock
  asr: mock       # mock | whisper
```

Nothing in v0.1 calls a paid API. Lip-sync is a fourth interface and it refuses to run.

## Video: mock

`satrcut render -e 1 --provider mock`

- No network, no GPU, no key.
- Writes a clearly fake clip at the plan size (480×848 by default): a solid grade color, a yellow bar that travels the frame, and the shot id.
- Muxes the line wav when one exists.
- QC sees real motion, so a mock take PASSes the stillness check. Hands, faces, and identity are reported as skipped unless MediaPipe or SFace weights are installed.

Use this for CI, for a laptop demo, and to prove resume, subtitles, and the sha256 manifest.

## Video: comfyui

`satrcut render -e 1 --provider comfyui`

Talks to a ComfyUI server over HTTP. ComfyUI is GPL-3.0 and is not vendored.

| Call | Use |
|---|---|
| `GET /system_stats` | Health. If it fails and `SATRCUT_COMFY_ROOT` is set, the runner starts `python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch` and retries the same seed once |
| `POST /upload/image` | Start frame, then end frame |
| `POST /prompt` | The Wan2.2 I2V A14B graph |
| `GET /history/{id}` | Poll until success or error |
| `GET /view` | Fetch frames, then ffmpeg encodes the mp4 |

The graph is the one in `seed/pipeline/wan14b_comfy.py`:

- High-noise and low-noise 14B fp8 experts
- lightx2v 4-step LoRAs
- cfg 1.0, 8 steps, split at 4, euler / simple
- `WanFirstLastFrameToVideo` when an end frame exists
- Optional character LoRAs from `shot["char_loras"]` (`approved: true` in a registry makes `L13` warn when one is missing)

Defaults that the linter is written for: 480×848, 16 fps, length 4n+1, at most 81 frames.

`satrcut models` prints filenames and the public sha256 pins for the 5B reference weights. The tool does not download them. A gated repo, if you fetch it yourself, needs `HF_TOKEN` in the environment. Do not put that token in a plan or a workflow JSON.

If the server dies mid-attempt, the runner restarts it and retries that seed once, then continues the seed ladder (`611, 733, 857, 919` in the demo, `max_tries` default 4).

## Adding a video provider

Implement the protocol in `satrcut.providers.base`:

```python
class VideoProvider(Protocol):
    name: str
    def healthy(self) -> bool: ...
    def restart(self) -> None: ...
    def render(self, request: RenderRequest) -> RenderResult: ...
```

Raise `satrcut.errors.InfraError` when the host or the server failed and the same seed should be retried. Raise anything else to record `RENDER_ERROR` and move to the next seed. Return a real mp4 path. Do not read secrets from the plan.

Register the name in `satrcut.providers.video_provider`. A cloud provider (fal and the rest) is phase 2 and must take its key from the environment.

## TTS

`satrcut.providers.tts.TTSProvider`:

```python
def synthesize(self, text: str, voice: str, dest: Path, *, duration_s: float | None = None) -> TTSResult
```

Mock writes a sine tone whose pitch depends on the voice id, and a `.txt` sidecar that contains the script line exactly. Duration follows the word count and is capped by the shot.

`plan generate` and `satrcut tts` both call this. Kokoro, Chatterbox, Qwen3, and ElevenLabs are phase 2. ElevenLabs would read `ELEVENLABS_API_KEY` and is never required.

Effects marked `synth: noise` become a short deterministic burst. `music` becomes a quiet two-note bed used for ducking.

## ASR

`satrcut.providers.asr.ASRProvider`:

```python
def transcribe(self, wav: Path, expected: str) -> ASRResult
```

Mock reads the sidecar and compares it to the script. It does not "fix" the line. `he` versus `she`, or any other substitution, is a mismatch and `satrcut tts` exits 1.

`asr: whisper` uses faster-whisper when that package is installed (`SATRCUT_WHISPER_MODEL`, default `tiny`). If it is not installed, the command tells you to install `satrcut[asr]` or stay on mock. A mismatch is still a flag. The script line is not rewritten.

## Lip-sync

`satrcut.lipsync.LipSyncProvider` is the only interface. `available()` is `False`. `apply()` raises `LipSyncUnavailable`.

```bash
satrcut lipsync
# exit 2
```

Do not auto-apply MuseTalk, LatentSync, or InfiniteTalk to a close-up. That pass was 0 of 12. Until a published fixture is green, `L04` rejects `line_mode: lipsync_cu` unless the plan sets `lipsync_approved: true`, and even then v0.1 does not run a model.

## What a provider must not do

- Write an API key, token, or cookie into `plan.json`, `workflow.json`, `gate_log.jsonl`, or a test fixture.
- Vendor ComfyUI or any other GPL tree.
- Download gated weights with a baked-in token.
- Delete `.satrcut/STOP` or `.satrcut/MAINT_WINDOW`.
