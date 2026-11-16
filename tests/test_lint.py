import importlib.util
from pathlib import Path

from satrcut.lint import (
    CONTACT,
    HAND,
    MOVE,
    NEGATION,
    Finding,
    lint_plan,
    lint_shots,
)
from satrcut.plan import generate_plan, write_plan
from satrcut.project import load_series
from satrcut.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _seed_shotlint():
    path = ROOT / "seed" / "v2" / "shotlint.py"
    spec = importlib.util.spec_from_file_location("seed_shotlint", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_regexes_match_the_seed():
    seed = _seed_shotlint()
    assert HAND == seed.HAND
    assert NEGATION == seed.NEGATION
    assert CONTACT == seed.CONTACT
    assert MOVE  # imported from the v5 rules; just ensure it is the camera-move pattern
    assert "creeps?" in MOVE


def test_seed_demo_plan_is_clean_at_shot_level():
    import json

    doc = json.loads((ROOT / "seed" / "examples" / "demo_ep01_plan.json").read_text(encoding="utf-8"))
    findings, _total = lint_shots(doc, episode=1, root=ROOT / "seed" / "examples")
    errors = [item for item in findings if item.level == "ERROR"]
    warnings = [item for item in findings if item.level == "WARN"]
    assert errors == []
    assert warnings == []


def _demo(tmp_path: Path):
    project = tmp_path / "demo"
    assert main(["--project", str(project), "init", str(project)]) == 0
    assert main(["--project", str(project), "plan", "generate", "-e", "1"]) == 0
    series = load_series(project)
    plan = generate_plan(series, 1)
    return project, plan


def test_demo_plan_lints_clean(tmp_path: Path):
    project, _plan = _demo(tmp_path)
    assert main(["--project", str(project), "lint", "-e", "1"]) == 0


def _mutate(plan: dict, shot_id: str, **changes):
    for shot in plan["shots"]:
        if shot["id"] == shot_id:
            shot.update(changes)
            if "prompt" not in changes:
                shot["prompt"] = shot.get("prompt", "") + " " + str(changes.get("action", ""))
            return shot
    raise AssertionError(shot_id)


def test_hands_negation_faces_endframe_wardrobe(tmp_path: Path):
    project, plan = _demo(tmp_path)
    hands = [dict(shot) for shot in plan["shots"]]
    plan["shots"] = hands
    _mutate(plan, "1-hook", action="Her hands grip the rail.", prompt=plan["shots"][0]["prompt"] + " Her hands grip the rail.")
    report = lint_plan(plan, project)
    assert any(item.rule == "L01 HANDS" and item.level == "ERROR" for item in report.findings)

    plan = generate_plan(load_series(project), 1)
    _mutate(plan, "2-ecu", prompt=plan["shots"][1]["prompt"] + " no wheel in frame")
    report = lint_plan(plan, project)
    assert any(item.rule == "L02 NEGATION" and item.level == "ERROR" for item in report.findings)

    plan = generate_plan(load_series(project), 1)
    _mutate(
        plan,
        "2-react",
        faces_visible=["alex", "jordan"],
        in_frame=["alex", "jordan"],
        shot_type="cu",
        prompt=plan["shots"][2]["prompt"] + " nose-to-nose",
        action="They stand nose-to-nose.",
    )
    report = lint_plan(plan, project)
    rules = {item.rule for item in report.errors}
    assert "L03 TWO_FACES" in rules or "L03 CONTACT" in rules

    plan = generate_plan(load_series(project), 1)
    shot = _mutate(plan, "3-cu", end_frame="", anchor="keyed")
    assert shot["end_frame"] == ""
    report = lint_plan(plan, project)
    assert any(item.rule == "L09 END_ANCHOR" and item.shot_id == "3-cu" for item in report.errors)

    plan = generate_plan(load_series(project), 1)
    plan["scenes"]["INT. STAIRWELL - NIGHT"]["wardrobe"]["alex"] = "a light linen shirt"
    plan["shots"].append(
        {
            **plan["shots"][1],
            "id": "9-change",
            "scene": "INT. STAIRWELL - NIGHT",
            "in_frame": ["alex"],
            "faces_visible": ["alex"],
            "place": "stairwell",
        }
    )
    findings, _ = lint_shots(plan, episode=1, root=project)
    assert any(isinstance(item, Finding) and item.rule == "L10 WARDROBE_CHANGE" for item in findings)
