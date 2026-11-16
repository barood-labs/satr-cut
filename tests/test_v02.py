"""v0.2: write, registry, studio, providers, dialogue, export presets."""

import json
import os
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from satrcut.cli import main
from satrcut.dialogue import plugins, route_plan, route_shot
from satrcut.export import delivery_for, loudnorm, preset, shape_subtitle
from satrcut.llm import MockLLM, OpenAICompatibleLLM, get_llm, parse_json_object
from satrcut.project import load_series
from satrcut.providers import video_provider
from satrcut.providers.base import RenderRequest
from satrcut.providers.workflows import build_graph, with_identity
from satrcut.registry import IdentityLock, apply_registry
from satrcut.studio import cost_report, decide, episode_payload, serve
from satrcut.util import read_json
from satrcut.write import autofix, write_episode


def _init(tmp_path: Path) -> Path:
    project = tmp_path / "show"
    assert main(["--project", str(project), "init", str(project)]) == 0
    return project


def test_write_mock_lints_and_autofix_repairs_hands(tmp_path: Path):
    project = _init(tmp_path)
    series = load_series(project)
    plan = write_episode(series, 1, premise="A rooftop secret, told in close-ups.", llm=MockLLM(), episodes=2)
    assert main(["--project", str(project), "lint", "-e", "1"]) == 0
    assert main(["--project", str(project), "lint", "-e", "2"]) == 0
    assert plan["emotional_beat"] in ("rupture", "confession", "threat", "reversal", "longing")
    assert plan["ending"] == "uncompleted_action"
    assert plan["cuts"][0]["beat"] == "hook"
    assert plan["cuts"][0]["dur"] <= 3
    assert 60 <= plan["runtime_s"] <= 90
    assert any(cut.get("source") == "card:next" for cut in plan["cuts"])
    assert any(cut.get("beat") == "reversal" for cut in plan["cuts"])
    for cut in plan["cuts"]:
        if str(cut.get("source") or "").startswith("card:") or cut.get("beat") == "hook":
            continue
        assert 1.5 <= float(cut["dur"]) <= 2.5
    poisoned = json.loads(json.dumps(plan))
    poisoned["shots"][0]["action"] = "Her hands grip the rail and there is no wheel."
    poisoned["shots"][0]["prompt"] += " Her hands grip the rail and there is no wheel."
    fixed, passes = autofix(poisoned, project)
    assert passes >= 1
    assert main(["--project", str(project), "lint", "-e", "1"]) == 0
    from satrcut.lint import lint_plan

    assert lint_plan(fixed, project).errors == []
    assert "hands" not in fixed["shots"][0]["prompt"].lower()


def test_llm_providers_do_not_embed_keys():
    assert get_llm("mock").name == "mock"
    brief = parse_json_object(MockLLM().complete("sys", "episode: 2\npremise: a quiet threat"))
    assert brief["emotional_beat"] == "confession"
    os.environ.pop("SATRCUT_LLM_API_KEY", None)
    try:
        OpenAICompatibleLLM().request("sys", "user")
    except Exception as exc:
        assert "SATRCUT_LLM_API_KEY" in str(exc)
        assert "sk-" not in str(exc)
    else:
        raise AssertionError("missing key should fail")
    os.environ["SATRCUT_LLM_API_KEY"] = "test-key-not-a-secret"
    try:
        _url, payload, headers = OpenAICompatibleLLM().request("sys", "user")
        assert "test-key-not-a-secret" not in json.dumps(payload)
        assert headers["Authorization"].endswith("test-key-not-a-secret")
    finally:
        os.environ.pop("SATRCUT_LLM_API_KEY", None)


def test_registry_and_identity_template(tmp_path: Path):
    project = _init(tmp_path)
    series = load_series(project)
    plan = write_episode(series, 1, llm=MockLLM())
    stamped = apply_registry(series, plan)
    shot = next(item for item in stamped["shots"] if item["faces_visible"])
    assert shot["qc"]["refs"]
    assert shot["identity_locks"]
    graph = build_graph("wan22-a14b-identity", shot, "start.png", "end.png", 611, "demo")
    assert graph["12"]["class_type"] == "WanFirstLastFrameToVideo"
    locked = with_identity({}, [IdentityLock("alex", "ipadapter", "assets/alex_sheet.png", None, 0.8)])
    assert locked["ipa_0"]["class_type"] == "IPAdapterAdvanced"
    pulid = with_identity({}, [IdentityLock("alex", "pulid", "assets/alex_sheet.png", None, 0.6)])
    assert pulid["pulid_0"]["class_type"] == "PuLIDApply"
    wan5 = build_graph("wan22-5b", shot, "start.png", None, 1, "p")
    assert wan5["1"]["inputs"]["unet_name"].startswith("wan2.2_ti2v_5B")
    ltx = build_graph("ltx", shot, "start.png", None, 1, "p")
    assert any(node.get("class_type") == "LTXVImgToVideo" for node in ltx.values())


def test_cloud_providers_do_not_bill_without_a_key(tmp_path: Path):
    os.environ.pop("FAL_KEY", None)
    os.environ.pop("REPLICATE_API_TOKEN", None)
    request = RenderRequest(
        shot={"id": "1", "prompt": "a face, lips sealed", "frames": 17, "width": 480, "height": 848},
        seed=1,
        preset={},
        start_frame=tmp_path / "a.png",
        end_frame=tmp_path / "b.png",
        out_dir=tmp_path,
    )
    for name in ("fal", "replicate"):
        provider = video_provider(name)
        assert provider.configured() is False
        try:
            provider.render(request)
        except Exception as exc:
            text = str(exc)
            assert "mock" in text
            assert "sk-" not in text
        else:
            raise AssertionError(name)


def test_dialogue_routes_lipsync_off_the_mouth(tmp_path: Path):
    project = _init(tmp_path)
    series = load_series(project)
    plan = write_episode(series, 1, llm=MockLLM())
    shot = next(item for item in plan["shots"] if item.get("line"))
    shot["line_mode"] = "lipsync_cu"
    shot["faces_visible"] = ["alex"]
    shot["speaker"] = "Alex"
    routed = route_shot(shot, "test")
    assert routed["line_mode"] in ("listener", "vo")
    assert "alex" not in [name.lower() for name in routed["faces_visible"]]
    assert "lips sealed" in routed["action"].lower()
    plan2, notes = route_plan(plan)
    assert notes
    from satrcut.lint import lint_plan

    assert lint_plan(plan2, project).errors == []
    gate = plugins()[0].decide(0.2)
    assert gate.decision == "unavailable"
    os.environ["SATRCUT_LATENTSYNC"] = str(project)
    try:
        scored = plugins()[0].decide(0.2)
        assert scored.decision == "route"
        assert plugins()[0].decide(0.95).decision == "apply"
    finally:
        os.environ.pop("SATRCUT_LATENTSYNC", None)


def test_export_presets_and_arabic_rtl():
    shaped = shape_subtitle("لا تجب", "ar")
    assert shaped.startswith("\u202b") and shaped.endswith("\u202c")
    assert shape_subtitle("Don't answer that.", "en") == "Don't answer that."
    for name in ("tiktok", "youtube_shorts", "reels", "reelshort"):
        spec = delivery_for({"delivery": {"width": 480, "height": 848, "fps": 16}, "lang": "en"}, name)
        assert (spec["width"], spec["height"]) == (1080, 1920)
        assert spec["lufs"] == -14
        assert loudnorm(-14) == "loudnorm=I=-14:TP=-1.5:LRA=11"
    assert preset("master")["lufs"] == -16


def test_studio_queue_cost_and_retake(tmp_path: Path):
    project = _init(tmp_path)
    series = load_series(project)
    write_episode(series, 1, llm=MockLLM())
    shot_dir = project / "renders" / "ep01" / "1-hook"
    shot_dir.mkdir(parents=True)
    (shot_dir / "manifest.json").write_text(
        json.dumps(
            {
                "shot_id": "1-hook",
                "status": "REVIEW",
                "attempts": [{"attempt": 1, "seed": 611, "verdict": "REVIEW", "render_s": 36, "reasons": ["HAND insert"]}],
                "chosen": {"attempt": 1, "seed": 611, "verdict": "REVIEW", "reasons": ["HAND insert"], "render_s": 36},
            }
        ),
        encoding="utf-8",
    )
    payload = episode_payload(project, 1)
    assert any(card["id"] == "1-hook" and "HAND" in " ".join(card["reasons"]) for card in payload["shots"])
    report = cost_report(project, 1)
    assert report["billed"] is False
    assert report["gpu_hours"] == 0.01
    decide(project, 1, "1-hook", "approve")
    assert read_json(shot_dir / "manifest.json")["human_accept"] is True
    decide(project, 1, "1-hook", "retake")
    assert not (shot_dir / "manifest.json").exists()
    httpd = serve(project, port=0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        health = json.loads(urlopen(f"http://127.0.0.1:{port}/api/health").read().decode())
        assert health["ok"] is True
        page = urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "Satr" in page and "Retake" in page
        body = json.dumps({"action": "reject"}).encode()
        request = Request(
            f"http://127.0.0.1:{port}/api/episode/1/shot/2-ecu",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        decision = json.loads(urlopen(request).read().decode())
        assert decision["action"] == "reject"
    finally:
        httpd.shutdown()
