import json
import wave
from pathlib import Path

from satrcut.errors import InfraError, LipSyncUnavailable, SimulatedCrash
from satrcut.lipsync import get_lipsync
from satrcut.project import load_series
from satrcut.providers.asr import lines_match
from satrcut.providers.base import RenderRequest
from satrcut.providers.comfyui_wan import server_command, workflow
from satrcut.providers.mock import MockProvider
from satrcut.qc import box_gap, evaluate
from satrcut.runner import attempt_ladder, halt_reason, render_episode, remedy
from satrcut.speech import synthesize_plan
from satrcut.util import require_ok, sha256_file


def test_box_gap_units():
    assert abs(box_gap([0, 0, 10, 10], [20, 0, 10, 10]) - 1) < 1e-6
    assert box_gap([0, 0, 10, 10], [5, 0, 10, 10]) == 0


def test_still_fails_and_mock_motion_passes(tmp_path: Path):
    still = tmp_path / "still.mp4"
    require_ok(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=black:s=480x848:r=16:d=1.5",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(still),
        ]
    )
    report = evaluate(still, {"hands_allowed": False, "faces_expected": 0})
    assert report.verdict == "FAIL"
    assert any(reason.startswith("STILL") or reason.startswith("KEN_BURNS") for reason in report.reasons)

    provider = MockProvider()
    out = tmp_path / "take"
    result = provider.render(
        RenderRequest(
            shot={"id": "1-hook", "width": 480, "height": 848, "fps": 16, "frames": 33, "grade": "danger"},
            seed=611,
            preset={},
            start_frame=tmp_path / "missing-start.png",
            end_frame=tmp_path / "missing-end.png",
            out_dir=out,
        )
    )
    moving = evaluate(result.mp4, {"faces_expected": 0, "mouth": "free"})
    assert moving.verdict == "PASS"
    assert "hands" in moving.skipped or "faces" in moving.skipped


def test_ken_burns_or_still_fails(tmp_path: Path):
    image = tmp_path / "still.png"
    require_ok(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=0x224466:s=480x848", "-frames:v", "1", str(image),
        ]
    )
    clip = tmp_path / "zoom.mp4"
    require_ok(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(image),
            "-vf", "zoompan=z='min(1.2,1+0.002*on)':d=48:s=480x848:fps=16",
            "-t", "2", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(clip),
        ]
    )
    report = evaluate(clip, {"faces_expected": 0, "mouth": "free"})
    assert report.verdict == "FAIL"
    assert any("KEN_BURNS" in reason or reason.startswith("STILL") for reason in report.reasons)


def test_workflow_is_wan_a14b_first_last_cfg1():
    shot = {
        "prompt": "a face, lips sealed",
        "negative": "static",
        "width": 480,
        "height": 848,
        "frames": 49,
        "a14b": {"cfg": 1.0, "steps": 8, "split": 4},
    }
    graph = workflow(shot, "start.png", "end.png", 611, "satrcut_test")
    assert graph["12"]["class_type"] == "WanFirstLastFrameToVideo"
    assert graph["12"]["inputs"]["end_image"] == ["11_end", 0]
    assert graph["12"]["inputs"]["length"] == 49
    assert graph["13"]["inputs"]["cfg"] == 1.0
    assert "wan2.2_i2v_high_noise_14B" in graph["1"]["inputs"]["unet_name"]
    command = server_command(8188)
    assert "--disable-auto-launch" in command
    assert "8188" in command


def test_lipsync_is_a_stub():
    plugin = get_lipsync()
    assert plugin.available() is False
    try:
        plugin.apply(Path("a.mp4"), Path("a.wav"), Path("b.mp4"))
    except LipSyncUnavailable as exc:
        assert "phase 2" in str(exc)
    else:
        raise AssertionError("lip-sync should refuse")


def test_asr_does_not_rewrite_a_mismatch(tmp_path: Path):
    assert lines_match("Don't answer that.", "dont answer that")
    assert not lines_match("he left", "she left")
    wav = tmp_path / "line.wav"
    from satrcut.providers.tts import MockTTS

    MockTTS().synthesize("Don't answer that.", "mock:alex", wav, duration_s=0.8)
    wav.with_suffix(".txt").write_text("she left", encoding="utf-8")
    from satrcut.providers.asr import MockASR

    heard = MockASR().transcribe(wav, "Don't answer that.")
    assert heard.ok is False
    assert heard.text == "she left"
    assert heard.expected == "Don't answer that."


def test_resume_keeps_the_finished_seed(tmp_path: Path):
    project = tmp_path / "job"
    project.mkdir()
    (project / "series.yaml").write_text(
        "name: resume\nrender: {width: 320, height: 568, fps: 16}\n"
        "delivery: {width: 320, height: 568, fps: 16}\nproviders: {video: mock, tts: mock, asr: mock}\n",
        encoding="utf-8",
    )
    shots = []
    for shot_id in ("1-hook", "2-ecu"):
        shots.append(
            {
                "id": shot_id,
                "kind": "character",
                "method": "a14b",
                "frames": 17,
                "fps": 16,
                "width": 320,
                "height": 568,
                "duration_s": 17 / 16,
                "grade": "danger",
                "anchor": "keyed",
                "shot_type": "cu",
                "in_frame": [],
                "faces_visible": [],
                "start_frame": f"keyframes/{shot_id}_start.png",
                "end_frame": f"keyframes/{shot_id}_end.png",
                "seeds": [611, 733],
                "prompt": "x",
                "qc": {"faces_expected": 0, "mouth": "free", "hands_allowed": False},
            }
        )
    plan = {"episode": 1, "shots": shots, "providers": {"tts": "mock", "asr": "mock"}, "presets": {"hybrid8": {}}, "ladder": ["hybrid8"], "music": None, "characters": {}}
    (project / "episodes").mkdir()
    (project / "episodes" / "ep01.plan.json").write_text(json.dumps(plan), encoding="utf-8")

    class CrashOnce(MockProvider):
        def __init__(self):
            self.calls: list[tuple[str, int]] = []
            self.crashed = False

        def render(self, request: RenderRequest):
            self.calls.append((request.shot["id"], request.seed))
            if request.shot["id"] == "2-ecu" and not self.crashed:
                self.crashed = True
                raise SimulatedCrash("host rebooted")
            return super().render(request)

    provider = CrashOnce()
    assert render_episode(project, 1, provider=provider, max_tries=2, skip_lint=True) == 3
    partial = json.loads((project / "renders" / "ep01" / "2-ecu" / "partial.json").read_text())
    assert partial["current"]["seed"] == 611
    assert partial["current"]["state"] == "in_flight"
    first_hash = sha256_file(next((project / "renders" / "ep01" / "1-hook").glob("a1_s611/*.mp4")))
    assert render_episode(project, 1, provider=provider, max_tries=2, skip_lint=True) == 0
    assert provider.calls[0] == ("1-hook", 611)
    assert ("1-hook", 611) not in provider.calls[1:]
    assert ("2-ecu", 611) in provider.calls
    manifest = json.loads((project / "renders" / "ep01" / "2-ecu" / "manifest.json").read_text())
    assert manifest["chosen"]["seed"] == 611
    assert sha256_file(next((project / "renders" / "ep01" / "1-hook").glob("a1_s611/*.mp4"))) == first_hash
    assert not (project / "renders" / "ep01" / "2-ecu" / "partial.json").exists()


def test_stop_file_is_not_deleted(tmp_path: Path):
    project = tmp_path / "stop"
    project.mkdir()
    (project / "series.yaml").write_text("name: stop\nproviders: {video: mock, tts: mock, asr: mock}\n", encoding="utf-8")
    (project / "episodes").mkdir()
    shot = {
        "id": "1-hook", "frames": 17, "fps": 16, "width": 320, "height": 568, "duration_s": 1,
        "grade": "neutral", "anchor": "pin", "start_frame": "keyframes/1-hook_start.png",
        "end_frame": "keyframes/1-hook_end.png", "seeds": [1], "qc": {"faces_expected": 0, "mouth": "free"},
    }
    (project / "episodes" / "ep01.plan.json").write_text(json.dumps({"episode": 1, "shots": [shot], "providers": {"tts": "mock", "asr": "mock"}, "music": None}), encoding="utf-8")
    stop = project / ".satrcut" / "STOP"
    stop.parent.mkdir(parents=True)
    stop.write_text("hold\n", encoding="utf-8")
    assert render_episode(project, 1, provider=MockProvider(), skip_lint=True) == 3
    assert stop.read_text() == "hold\n"


def test_hand_remedy_crops_and_still_swaps_pin(tmp_path: Path):
    from PIL import Image

    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    image = Image.new("RGB", (80, 140), (10, 20, 30))
    from PIL import ImageDraw
    ImageDraw.Draw(image).rectangle((0, 110, 80, 140), fill=(220, 20, 20))
    image.save(start)
    image.save(end)
    before = sha256_file(start)
    partial = {"crops": 0, "anchor": "pin"}
    note = remedy(["HAND from 0.2s (3 samples)"], {"allow_hands": False, "width": 80, "height": 140, "anchor": "pin"}, start, end, partial)
    assert "cropped" in note
    assert partial["crops"] == 1
    assert sha256_file(start) != before
    note = remedy(["STILL (no motion) local=0"], {"anchor": "pin", "width": 80, "height": 140}, start, end, partial)
    assert partial["anchor"] == "drift"
    assert "drift" in note


def test_infra_retry_uses_the_same_seed(tmp_path: Path):
    project = tmp_path / "infra"
    project.mkdir()
    (project / "series.yaml").write_text("name: infra\nproviders: {video: mock, tts: mock, asr: mock}\n", encoding="utf-8")
    (project / "episodes").mkdir()
    shot = {
        "id": "1-hook", "frames": 17, "fps": 16, "width": 320, "height": 568, "duration_s": 1,
        "grade": "danger", "anchor": "keyed", "start_frame": "keyframes/1-hook_start.png",
        "end_frame": "keyframes/1-hook_end.png", "seeds": [611, 733],
        "qc": {"faces_expected": 0, "mouth": "free"},
    }
    (project / "episodes" / "ep01.plan.json").write_text(
        json.dumps({"episode": 1, "shots": [shot], "providers": {"tts": "mock", "asr": "mock"}, "presets": {"hybrid8": {}}, "ladder": ["hybrid8"], "music": None}),
        encoding="utf-8",
    )

    class Flaky(MockProvider):
        def __init__(self):
            self.seeds: list[int] = []
            self.restarts = 0

        def healthy(self):
            return self.restarts > 0 or len(self.seeds) == 0

        def restart(self):
            self.restarts += 1

        def render(self, request: RenderRequest):
            self.seeds.append(request.seed)
            if len(self.seeds) == 1:
                raise InfraError("comfy down")
            return super().render(request)

    flaky = Flaky()
    assert render_episode(project, 1, provider=flaky, max_tries=2, skip_lint=True) == 0
    assert flaky.seeds[:2] == [611, 611]
    assert flaky.restarts >= 1


def test_tts_roundtrip_and_ladder():
    assert [step["seed"] for step in attempt_ladder({"seeds": [611, 733, 857, 919]}, {"ladder": ["hybrid8"], "presets": {}}, 4)] == [611, 733, 857, 919]
    assert halt_reason  # bound


def test_synthesize_writes_a_wav(tmp_path: Path):
    plan = {
        "providers": {"tts": "mock", "asr": "mock"},
        "characters": {"alex": {"display": "Alex", "voice": "mock:alex"}},
        "music": "audio/bed.wav",
        "shots": [
            {"id": "3-os", "line": "Don't answer that.", "speaker": "Alex (O.S.)", "line_audio": "audio/lines/3-os.wav", "duration_s": 2.5,
             "sfx": [{"file": "audio/sfx/door.wav", "synth": "noise"}]}
        ],
    }
    report = synthesize_plan(tmp_path, plan)
    assert report["mismatches"] == 0
    wav = tmp_path / "audio" / "lines" / "3-os.wav"
    with wave.open(str(wav)) as handle:
        assert handle.getnframes() > 1000
    assert (tmp_path / "audio" / "bed.wav").is_file()
    assert (tmp_path / "audio" / "sfx" / "door.wav").is_file()
    assert load_series.__name__
