"""Mock provider, end to end: init, lint, render, qc, assemble."""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from satrcut.cli import main
from satrcut.service import install
from satrcut.util import media_duration, require_ok, video_size


def test_mock_episode_assembles_and_refuses_a_bad_manifest(tmp_path: Path):
    project = tmp_path / "demo-hook"
    assert main(["--project", str(project), "init", str(project)]) == 0
    assert main(["--project", str(project), "plan", "generate", "-e", "1"]) == 0
    assert main(["--project", str(project), "lint", "-e", "1"]) == 0
    assert main(["--project", str(project), "tts", "-e", "1"]) == 0
    assert main(["--project", str(project), "render", "-e", "1", "--provider", "mock"]) == 0
    assert main(["--project", str(project), "render", "-e", "1", "--provider", "mock"]) == 0
    assert main(["--project", str(project), "status", "-e", "1"]) == 0
    shot = next((project / "renders" / "ep01").glob("*/manifest.json"))
    manifest = json.loads(shot.read_text(encoding="utf-8"))
    take = project / manifest["chosen"]["mp4"]
    assert main(["--project", str(project), "qc", str(take), "--shot", manifest["shot_id"]]) == 0
    assert main(["--project", str(project), "assemble", "-e", "1"]) == 0

    mp4 = project / "deliverables" / "ep01.mp4"
    assert mp4.is_file()
    assert video_size(mp4) == (1080, 1920)
    duration = media_duration(mp4)
    assert 18 < duration < 30
    assert (project / "deliverables" / "ep01.jpg").stat().st_size > 1000
    assert (project / "deliverables" / "ep01.srt").read_text(encoding="utf-8").count("Don't answer that.") == 1
    report = json.loads((project / "deliverables" / "ep01.report.json").read_text(encoding="utf-8"))
    assert "sidechaincompress" in report["audio_filter"]
    assert report["shippable"] is True

    plan = json.loads((project / "episodes" / "ep01.plan.json").read_text(encoding="utf-8"))
    frame = tmp_path / "sub.png"
    require_ok(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{float(plan['stakes_at_s']) + 0.35:.3f}",
            "-i", str(mp4), "-frames:v", "1", str(frame),
        ]
    )
    image = np.asarray(Image.open(frame).convert("RGB"))
    band = image[1300:1850, 80:1000]
    yellow = (band[:, :, 0] > 180) & (band[:, :, 1] > 150) & (band[:, :, 2] < 140)
    assert int(yellow.sum()) > 20

    frozen = json.loads((project / "deliverables" / "ep01.manifest.json").read_text(encoding="utf-8"))
    frozen["takes"][0]["sha256"] = "0" * 64
    (project / "deliverables" / "ep01.manifest.json").write_text(json.dumps(frozen), encoding="utf-8")
    assert main(["--project", str(project), "assemble", "-e", "1", "--no-freeze"]) == 1

    text = install(project, 1, "comfyui")
    assert "schtasks /Create" in text
    unit = (project / "deploy" / "satrcut-render.service").read_text(encoding="utf-8")
    assert "Restart=on-failure" in unit
    assert "comfyui" in unit
