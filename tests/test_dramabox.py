"""DramaBox-style preset: pacing, tropes, packaging, and the score gate."""

import json
from pathlib import Path

from satrcut.cli import main
from satrcut.export import delivery_for
from satrcut.score import score_episode
from satrcut.style import DRAMABOX_LOOK
from satrcut.write import paywall_episode, trope_brief, write_episode
from satrcut.project import load_series


def test_tropes_and_paywall_cadence():
    assert paywall_episode(6) and paywall_episode(12)
    assert not paywall_episode(5) and not paywall_episode(7)
    revenge = trope_brief("revenge", 1)
    wolf = trope_brief("werewolf", 6)
    assert revenge["stakes_line"] != wolf["stakes_line"]
    assert wolf["paywall"] is True
    assert wolf["last_line"] == wolf["paywall_line"]
    assert "contrast" in DRAMABOX_LOOK and "colorbalance" in DRAMABOX_LOOK


def test_score_passes_a_written_episode_and_flags_a_broken_one(tmp_path: Path):
    project = tmp_path / "show"
    assert main(["--project", str(project), "init", str(project)]) == 0
    series = load_series(project)
    plan = write_episode(series, 1, trope="contract", llm=__import__("satrcut.llm", fromlist=["MockLLM"]).MockLLM())
    assert plan["trope"] == "contract"
    assert main(["--project", str(project), "score", "-e", "1"]) == 0
    report = score_episode(project, 1)
    assert report["ok"] is True
    assert report["checks"]["cliffhanger"] is True
    assert report["checks"]["next_episode"] is True

    broken = json.loads(json.dumps(plan))
    broken["cuts"][0]["beat"] = "idle"
    broken["cuts"][0]["dur"] = 8
    broken["ending"] = "none"
    for cut in broken["cuts"]:
        if cut.get("source") == "card:next":
            cut["source"] = "card:extra"
        if cut.get("beat") == "reversal":
            cut["beat"] = "hold"
    (project / "episodes" / "ep01.plan.json").write_text(json.dumps(broken), encoding="utf-8")
    bad = score_episode(project, 1)
    assert bad["ok"] is False
    text = " ".join(bad["misses"])
    assert "hook" in text and "cliffhanger" in text and "stinger" in text and "reversal" in text


def test_package_makes_original_art_and_a_trailer(tmp_path: Path):
    project = tmp_path / "show"
    assert main(["--project", str(project), "init", str(project)]) == 0
    write_episode(load_series(project), 1, trope="billionaire")
    assert main(["--project", str(project), "package", "-e", "1"]) == 0
    assert (project / "deliverables" / "ep01_title.png").stat().st_size > 1000
    assert (project / "deliverables" / "poster.png").stat().st_size > 1000
    assert (project / "deliverables" / "trailer.mp4").stat().st_size > 1000
    spec = delivery_for({"style": "dramabox", "delivery": {}}, None)
    assert (spec["width"], spec["height"], spec["lufs"]) == (1080, 1920, -14.0)
    assert spec["safe"]["margin_v"] == 500
    assert spec["look"]