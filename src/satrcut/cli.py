"""satrcut command line."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from satrcut import __version__
from satrcut.assemble import assemble
from satrcut.errors import AssembleError, LipSyncUnavailable, SatrcutError
from satrcut.keyframes import check_image, register, write_briefs
from satrcut.lint import lint_plan
from satrcut.plan import write_plan
from satrcut.project import data_dir, episode_plan_path, find_project, load_series
from satrcut.providers.comfyui_wan import describe_models
from satrcut.qc import evaluate
from satrcut.runner import collect_status, format_status, job_dir_for, render_episode
from satrcut.service import install as install_service
from satrcut.service import run_service
from satrcut.speech import synthesize_plan
from satrcut.store import JobStore
from satrcut.util import read_json


def _project(args: argparse.Namespace) -> Path:
    return find_project(args.project)


def _episode(args: argparse.Namespace) -> int:
    return int(args.episode)


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.dir or ".").resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / "series.yaml").exists() and not args.force:
        raise SatrcutError(f"{dest / 'series.yaml'} already exists (pass --force to replace the templates)")
    (dest / "episodes").mkdir(exist_ok=True)
    shutil.copy(data_dir() / "series.yaml", dest / "series.yaml")
    shutil.copy(data_dir() / "ep01.yaml", dest / "episodes" / "ep01.yaml")
    (dest / ".gitignore").write_text(".satrcut/\nrenders/\ndeliverables/\naudio/\nkeyframes/\nassets/\n", encoding="utf-8")
    print(f"initialized {dest}")
    print("next: satrcut plan generate -e 1 && satrcut lint -e 1 && satrcut render -e 1 --provider mock && satrcut assemble -e 1")
    return 0


def cmd_plan_generate(args: argparse.Namespace) -> int:
    series = load_series(_project(args))
    path = write_plan(series, _episode(args))
    print(f"wrote {path}")
    return 0


def _load_plan(args: argparse.Namespace) -> tuple[Path, dict]:
    project = _project(args)
    if getattr(args, "plan", None):
        path = Path(args.plan)
        return path.parent if (path.parent / "series.yaml").exists() else project, read_json(path)
    path = episode_plan_path(project, _episode(args))
    if not path.is_file():
        raise SatrcutError(f"no plan at {path}. Run: satrcut plan generate -e {_episode(args)}")
    return project, read_json(path)


def cmd_lint(args: argparse.Namespace) -> int:
    project, plan = _load_plan(args)
    root = project if (project / "series.yaml").exists() else Path(args.plan).resolve().parent if getattr(args, "plan", None) else project
    if getattr(args, "plan", None):
        root = Path(args.plan).resolve().parent
    report = lint_plan(plan, root, episode=plan.get("episode"))
    print(report.render())
    return 1 if report.errors else 0


def cmd_keyframes_brief(args: argparse.Namespace) -> int:
    project, plan = _load_plan(args)
    paths = write_briefs(project, plan)
    print(f"wrote {len(paths)} briefs in {project / 'keyframes' / 'briefs'}")
    return 0


def cmd_keyframes_check(args: argparse.Namespace) -> int:
    project, plan = _load_plan(args)
    shot = next((item for item in plan.get("shots") or [] if item["id"] == args.shot), None)
    result = check_image(Path(args.image), shot)
    print(result)
    return 0 if result.get("ok") else 3


def cmd_keyframes_register(args: argparse.Namespace) -> int:
    project, plan = _load_plan(args)
    shot = next((item for item in plan.get("shots") or [] if item["id"] == args.shot), {})
    image = Path(args.image)
    if not image.is_absolute():
        image = project / image
    result = register(project, image, args.shot, args.role, shot=shot, force=args.force)
    print(f"registered {args.shot} {args.role}")
    if result.get("skipped"):
        print("skipped checks:", ", ".join(result["skipped"]))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    return render_episode(
        _project(args),
        _episode(args),
        provider_name=args.provider,
        max_tries=args.max_tries,
        retry_failed=args.retry_failed,
    )


def cmd_qc(args: argparse.Namespace) -> int:
    spec = {}
    if args.shot:
        _project_path, plan = _load_plan(args)
        shot = next((item for item in plan.get("shots") or [] if item["id"] == args.shot), None)
        if shot:
            spec = shot.get("qc") or {}
    out = Path(args.out) if args.out else Path(args.path).with_suffix(".qc.json")
    report = evaluate(Path(args.path), spec, out=out)
    print(f"{report.verdict:6} {args.path}")
    for reason in report.reasons:
        print(f"  {reason}")
    if report.skipped:
        print("skipped:", ", ".join(report.skipped))
    print(f"wrote {out}")
    return 0 if report.verdict in ("PASS", "TRIM", "REVIEW") else 3


def cmd_assemble(args: argparse.Namespace) -> int:
    path = assemble(
        _project(args),
        _episode(args),
        no_freeze=args.no_freeze,
        allow_human=args.allow_human_accept,
        allow_fail=args.allow_fail,
    )
    print(path)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project = _project(args)
    episode = _episode(args)
    rows = collect_status(project, episode)
    store = JobStore(project / ".satrcut" / "jobs.sqlite")
    try:
        store.refresh_from_job(episode, job_dir_for(project, episode))
    finally:
        store.close()
    print(format_status(rows))
    return 0


def cmd_tts(args: argparse.Namespace) -> int:
    project, plan = _load_plan(args)
    report = synthesize_plan(project, plan)
    print(f"tts={report['backend_tts']} asr={report['backend_asr']} lines={len(report['lines'])} mismatches={report['mismatches']}")
    for line in report["lines"]:
        flag = "ok" if line["ok"] else "MISMATCH"
        print(f"  {flag:8} {line['shot']:10} {line['transcript']}")
    return 1 if report["mismatches"] else 0


def cmd_service_install(args: argparse.Namespace) -> int:
    print(install_service(_project(args), _episode(args), args.provider or "mock"))
    return 0


def cmd_service_run(args: argparse.Namespace) -> int:
    return run_service(_project(args), _episode(args), args.provider or "mock")


def cmd_lipsync(_args: argparse.Namespace) -> int:
    from satrcut.lipsync import get_lipsync

    plugin = get_lipsync()
    print(f"{plugin.name}: available={plugin.available()}")
    try:
        plugin.apply(Path("."), Path("."), Path("."))
    except LipSyncUnavailable as exc:
        print(exc)
    return 2


def cmd_models(_args: argparse.Namespace) -> int:
    print(describe_models())
    return 0


def _add_episode(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-e", "--episode", type=int, default=1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="satrcut",
        description="Lint a vertical micro-drama, render it on ComfyUI or offline, QC every take, and assemble a 1080x1920 episode.",
    )
    parser.add_argument("--project", type=Path, help="series directory (default: SATRCUT_PROJECT or the nearest series.yaml)")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    init = sub.add_parser("init", help="write series.yaml and a demo episode")
    init.add_argument("dir", nargs="?", default=".")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    plan = sub.add_parser("plan", help="generate or lint a shot plan")
    plan_sub = plan.add_subparsers(dest="plan_cmd", required=True)
    generate = plan_sub.add_parser("generate", help="expand the episode yaml into plan json and mock audio")
    _add_episode(generate)
    generate.set_defaults(func=cmd_plan_generate)
    plan_lint = plan_sub.add_parser("lint", help="shotlint, exit 1 on errors")
    _add_episode(plan_lint)
    plan_lint.add_argument("--plan", type=Path)
    plan_lint.set_defaults(func=cmd_lint)

    lint = sub.add_parser("lint", help="shotlint, exit 1 on errors")
    _add_episode(lint)
    lint.add_argument("--plan", type=Path)
    lint.set_defaults(func=cmd_lint)

    frames = sub.add_parser("keyframes", help="briefs, checks, and the sha256 register")
    frames_sub = frames.add_subparsers(dest="frames_cmd", required=True)
    brief = frames_sub.add_parser("brief")
    _add_episode(brief)
    brief.set_defaults(func=cmd_keyframes_brief)
    check = frames_sub.add_parser("check")
    check.add_argument("image")
    check.add_argument("--shot", required=True)
    _add_episode(check)
    check.add_argument("--plan", type=Path)
    check.set_defaults(func=cmd_keyframes_check)
    reg = frames_sub.add_parser("register")
    reg.add_argument("image")
    reg.add_argument("--shot", required=True)
    reg.add_argument("--role", choices=("start", "end"), required=True)
    reg.add_argument("--force", action="store_true")
    _add_episode(reg)
    reg.add_argument("--plan", type=Path)
    reg.set_defaults(func=cmd_keyframes_register)

    render = sub.add_parser("render", help="resume-safe render for one episode")
    _add_episode(render)
    render.add_argument("--provider", choices=("mock", "comfyui"), default=None)
    render.add_argument("--max-tries", type=int, default=4)
    render.add_argument("--retry-failed", action="store_true")
    render.set_defaults(func=cmd_render)

    qc = sub.add_parser("qc", help="score one take")
    qc.add_argument("path")
    qc.add_argument("--shot")
    qc.add_argument("--out")
    _add_episode(qc)
    qc.add_argument("--plan", type=Path)
    qc.set_defaults(func=cmd_qc)

    built = sub.add_parser("assemble", help="freeze the manifest and build the episode")
    _add_episode(built)
    built.add_argument("--no-freeze", action="store_true")
    built.add_argument("--allow-human-accept", action="store_true")
    built.add_argument("--allow-fail", action="store_true")
    built.set_defaults(func=cmd_assemble)

    status = sub.add_parser("status", help="per-shot attempt table")
    _add_episode(status)
    status.set_defaults(func=cmd_status)

    tts = sub.add_parser("tts", help="synthesize lines and run the ASR check")
    _add_episode(tts)
    tts.add_argument("--plan", type=Path)
    tts.set_defaults(func=cmd_tts)

    service = sub.add_parser("service", help="install or run the resume loop as a service")
    service_sub = service.add_subparsers(dest="service_cmd", required=True)
    for name, func in (("install", cmd_service_install), ("run", cmd_service_run)):
        item = service_sub.add_parser(name)
        _add_episode(item)
        item.add_argument("--provider", default="mock")
        item.set_defaults(func=func)

    lipsync = sub.add_parser("lipsync", help="phase-2 lip-sync interface (off)")
    lipsync.set_defaults(func=cmd_lipsync)

    models = sub.add_parser("models", help="print public Wan weight filenames and checksums")
    models.set_defaults(func=cmd_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args))
    except (SatrcutError, AssembleError, LipSyncUnavailable, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
