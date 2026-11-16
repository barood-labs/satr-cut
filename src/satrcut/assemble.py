"""Freeze a sha256 manifest and build one 1080x1920 episode.

Build refuses if a take or the plan changed since freeze. Subtitles are burned
in yellow with a black outline. Dialogue, effects, and a music bed are mixed,
and the bed is ducked under the voices.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from satrcut import style
from satrcut.errors import AssembleError
from satrcut.project import episode_plan_path, load_series
from satrcut.runner import job_dir_for
from satrcut.util import (
    atomic_write_json,
    ffmpeg_filter_path,
    find_font,
    media_duration,
    now_iso,
    read_json,
    require_ok,
    sha256_file,
    video_size,
    x264_preset,
)

ACCEPTED = {"PASS", "TRIM"}


def manifest_path(project: Path, episode: int) -> Path:
    return project / "deliverables" / f"ep{episode:02d}.manifest.json"


def output_paths(project: Path, episode: int) -> dict[str, Path]:
    folder = project / "deliverables"
    stem = f"ep{episode:02d}"
    return {
        "mp4": folder / f"{stem}.mp4",
        "jpg": folder / f"{stem}.jpg",
        "srt": folder / f"{stem}.srt",
        "ass": folder / f"{stem}.ass",
        "report": folder / f"{stem}.report.json",
        "manifest": folder / f"{stem}.manifest.json",
    }


def _shot_id(cut: dict[str, Any]) -> str:
    if cut.get("shot_id"):
        return str(cut["shot_id"])
    source = str(cut.get("source") or "")
    return source.split(":", 1)[1] if ":" in source else source


def freeze(project: Path, episode: int, *, allow_human: bool = False, allow_fail: bool = False) -> dict[str, Any]:
    plan_path = episode_plan_path(project, episode)
    if not plan_path.is_file():
        raise AssembleError(f"no plan at {plan_path}")
    plan = read_json(plan_path)
    problems: list[str] = []
    takes: list[dict[str, Any]] = []
    folder = job_dir_for(project, episode)
    for cut in plan.get("cuts") or []:
        if cut.get("source") == "card:time":
            continue
        shot_id = _shot_id(cut)
        manifest = folder / shot_id / "manifest.json"
        if not manifest.is_file():
            problems.append(f"{shot_id}: no manifest")
            continue
        data = read_json(manifest)
        status = data.get("status")
        allowed = set(ACCEPTED)
        if allow_human:
            allowed.add("REVIEW")
        if allow_fail:
            allowed.add("FAIL_ALL")
        if status not in allowed:
            problems.append(f"{shot_id}: status {status} is not an accepted take")
            continue
        chosen = data.get("chosen") or {}
        if not chosen.get("mp4"):
            problems.append(f"{shot_id}: manifest has no chosen file")
            continue
        path = project / str(chosen["mp4"])
        if not path.is_file():
            problems.append(f"{shot_id}: missing {chosen['mp4']}")
            continue
        digest = sha256_file(path)
        if chosen.get("sha256") and digest != chosen["sha256"]:
            problems.append(f"{shot_id}: file changed since the render manifest")
        takes.append(
            {
                "shot_id": shot_id,
                "source": cut.get("source"),
                "file": str(chosen["mp4"]).replace("\\", "/"),
                "sha256": digest,
                "verdict": chosen.get("verdict") or status,
                "trim": chosen.get("trim"),
                "seed": chosen.get("seed"),
            }
        )
    document = {
        "version": 1,
        "episode": episode,
        "plan": plan_path.relative_to(project).as_posix(),
        "plan_sha256": sha256_file(plan_path),
        "frozen": now_iso(),
        "delivery": plan.get("delivery") or {"width": 1080, "height": 1920, "fps": 24},
        "takes": takes,
        "cut_ids": [_shot_id(cut) if cut.get("source") != "card:time" else "card:time" for cut in plan.get("cuts") or []],
        "problems": problems,
    }
    atomic_write_json(manifest_path(project, episode), document)
    return document


def verify(project: Path, document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("problems"):
        errors.extend(str(item) for item in document["problems"])
    plan_path = project / str(document["plan"])
    if not plan_path.is_file():
        errors.append(f"plan missing: {document['plan']}")
        return errors
    if sha256_file(plan_path) != document.get("plan_sha256"):
        errors.append("shot plan changed since freeze")
    plan = read_json(plan_path)
    expected = [_shot_id(cut) if cut.get("source") != "card:time" else "card:time" for cut in plan.get("cuts") or []]
    if expected != list(document.get("cut_ids") or []):
        errors.append("cut list changed since freeze")
    by_id = {row["shot_id"]: row for row in document.get("takes") or []}
    for cut in plan.get("cuts") or []:
        if cut.get("source") == "card:time":
            continue
        shot_id = _shot_id(cut)
        row = by_id.get(shot_id)
        if row is None:
            errors.append(f"{shot_id}: not in the frozen manifest")
            continue
        path = project / row["file"]
        if not path.is_file():
            errors.append(f"{shot_id}: missing {row['file']}")
        elif sha256_file(path) != row["sha256"]:
            errors.append(f"{shot_id}: sha256 mismatch for {row['file']}")
    return errors


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    remain = seconds % 60
    return f"{hours}:{minutes:02d}:{remain:05.2f}"


def write_ass(plan: dict[str, Any], path: Path, width: int, height: int) -> None:
    sub = style.EDIT["subtitle"]
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,DejaVu Sans,{sub['size']},{sub['colour']},&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{sub['outline']},0,{sub['align']},80,80,{sub['margin_v']},1",
        "Style: Name,DejaVu Sans,68,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,8,40,40,200,1",
        "Style: Time,DejaVu Serif,78,&H000000FF,&H000000FF,&H00000000,&H64000000,0,-1,0,0,100,100,0,0,1,2,0,5,40,40,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cut in plan.get("cuts") or []:
        start = float(cut.get("at") or 0)
        if cut.get("source") == "card:time" and cut.get("text"):
            lines.append(
                f"Dialogue: 1,{_ass_time(start)},{_ass_time(start + float(cut['dur']))},Time,,0,0,0,,{{\\fad(120,180)}}{cut['text']}"
            )
        name = cut.get("name")
        if name:
            label = name[0] if isinstance(name, list) else str(name)
            lines.append(
                f"Dialogue: 2,{_ass_time(start + 0.15)},{_ass_time(start + min(float(cut['dur']), 1.8))},Name,,0,0,0,,{{\\fad(150,200)}}{label}"
            )
        for line in cut.get("lines") or []:
            text = str(line.get("subtitle") or line.get("text") or "")
            if not text:
                continue
            at = start + float(line.get("at") or 0)
            audio = line.get("file")
            duration = 1.6
            if audio and (path.parents[1] / audio).exists():
                pass
            # Duration is filled by the caller via line['dur'] when known.
            duration = float(line.get("dur") or duration)
            lines.append(f"Dialogue: 0,{_ass_time(at)},{_ass_time(at + duration + 0.2)},Sub,,0,0,0,,{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_srt(plan: dict[str, Any], path: Path) -> None:
    rows = []
    index = 1
    for cut in plan.get("cuts") or []:
        start = float(cut.get("at") or 0)
        for line in cut.get("lines") or []:
            text = str(line.get("subtitle") or line.get("text") or "")
            if not text:
                continue
            at = start + float(line.get("at") or 0)
            duration = float(line.get("dur") or 1.6)
            rows.append(f"{index}\n{_srt_time(at)} --> {_srt_time(at + duration)}\n{text}\n")
            index += 1
    path.write_text("\n".join(rows), encoding="utf-8")


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    remain = seconds % 60
    millis = int(round((remain - int(remain)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{int(remain):02d},{millis:03d}"


def _attach_line_durations(project: Path, plan: dict[str, Any]) -> None:
    import wave

    for cut in plan.get("cuts") or []:
        for line in cut.get("lines") or []:
            audio = line.get("file")
            if not audio:
                continue
            path = project / str(audio)
            if not path.is_file():
                continue
            with wave.open(str(path), "r") as handle:
                line["dur"] = handle.getnframes() / float(handle.getframerate() or 1)


def static_checks(project: Path, plan: dict[str, Any]) -> list[str]:
    issues = []
    for cut in plan.get("cuts") or []:
        visible = {str(name).split(" (")[0].lower() for name in cut.get("faces_visible") or []}
        for line in cut.get("lines") or []:
            if not line.get("file"):
                issues.append(f"cut {cut.get('n')}: line has no audio file")
            elif not (project / str(line["file"])).is_file():
                issues.append(f"cut {cut.get('n')}: missing audio {line['file']}")
            speaker = str(line.get("speaker") or "").split(" (")[0].lower()
            mode = line.get("mode") or cut.get("line_mode")
            if speaker and mode in ("os", "vo", "listener") and speaker in visible:
                issues.append(f"cut {cut.get('n')}: {speaker}'s line plays on their own face")
    return issues


def _encode_segment(cut: dict[str, Any], src: Path | None, dest: Path, width: int, height: int, fps: int, font: Path) -> None:
    duration = float(cut["dur"])
    preset = x264_preset()
    if cut.get("source") == "card:time" or src is None:
        # The red time card is burned from the ASS track so it is not drawn twice.
        require_ok(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration:.3f}",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", preset, "-crf", "20",
                str(dest),
            ]
        )
        return
    piece = list(cut.get("piece") or [0, duration])
    if cut.get("_trim"):
        piece = list(cut["_trim"])
    start = float(piece[0])
    length = max(0.2, float(piece[1]) - start)
    push = cut.get("push") or [1.0, 1.04]
    z0, z1 = float(push[0]), float(push[1])
    span = max(duration, 0.1)
    zoom = f"({z0}+({z1}-{z0})*min(t/{span:.4f}\\,1))"
    grade = style.GRADE_FX.get(str(cut.get("grade") or "neutral"), style.GRADE_FX["neutral"])
    video_filter = ",".join(
        [
            f"fps={fps}",
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
            f"scale=w='trunc({width}*{zoom}/2)*2':h='trunc({height}*{zoom}/2)*2':eval=frame",
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2",
            grade,
            "setsar=1",
            "format=yuv420p",
        ]
    )
    require_ok(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-i", str(src),
            "-t", f"{length:.3f}", "-an", "-vf", video_filter,
            "-r", str(fps), "-c:v", "libx264", "-preset", preset, "-crf", "20",
            str(dest),
        ]
    )
    got = media_duration(dest)
    if got < duration - 0.08:
        padded = dest.with_suffix(".pad.mp4")
        require_ok(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(dest),
                "-vf", f"tpad=stop_mode=clone:stop_duration={duration - got + 0.08:.3f}",
                "-t", f"{duration:.3f}", "-an",
                "-c:v", "libx264", "-preset", preset, "-crf", "20",
                str(padded),
            ]
        )
        padded.replace(dest)


def mix_audio(project: Path, plan: dict[str, Any], total: float, dest: Path) -> str:
    events: list[tuple[Path, float, float]] = []
    for cut in plan.get("cuts") or []:
        start = float(cut.get("at") or 0)
        for line in cut.get("lines") or []:
            if line.get("file"):
                path = project / str(line["file"])
                if path.is_file():
                    events.append((path, start + float(line.get("at") or 0), 1.0))
        for effect in cut.get("sfx") or []:
            if effect.get("file"):
                path = project / str(effect["file"])
                if path.is_file():
                    events.append((path, start + float(effect.get("at") or 0), float(effect.get("gain") or 0.8)))
    bed = project / str(plan["music"]) if plan.get("music") else None
    if bed is not None and not bed.is_file():
        bed = None
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    filters: list[str] = []
    for index, (path, at, gain) in enumerate(events):
        cmd += ["-i", str(path)]
        delay = int(max(0.0, at) * 1000)
        filters.append(
            f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={gain:.3f},adelay={delay}|{delay}[e{index}]"
        )
    next_index = len(events)
    if bed is not None:
        cmd += ["-i", str(bed)]
        filters.append(
            f"[{next_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.20,atrim=0:{total:.3f},asetpts=PTS-STARTPTS[bed]"
        )
    if events:
        filters.append("".join(f"[e{index}]" for index in range(len(events))) + f"amix=inputs={len(events)}:normalize=0:duration=longest[vox]")
        if bed is not None:
            filters.append("[vox]asplit=2[vox1][vox2]")
            filters.append("[bed][vox2]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=300[ducked]")
            filters.append(f"[vox1][ducked]amix=inputs=2:normalize=0:duration=first,atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[out]")
        else:
            filters.append(f"[vox]atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[out]")
    elif bed is not None:
        filters.append(f"[bed]atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[out]")
    else:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", f"anullsrc=r=48000:cl=stereo", "-t", f"{total:.3f}", str(dest),
        ]
        require_ok(cmd)
        return "anullsrc"
    graph = ";".join(filters)
    cmd += ["-filter_complex", graph, "-map", "[out]", "-t", f"{total:.3f}", str(dest)]
    try:
        require_ok(cmd)
    except RuntimeError:
        fallback = graph.replace(",loudnorm=I=-16:TP=-1.5:LRA=11", "")
        cmd_fallback = [arg if arg != graph else fallback for arg in cmd]
        require_ok(cmd_fallback)
        return fallback
    return graph


def assemble(
    project: Path,
    episode: int = 1,
    *,
    no_freeze: bool = False,
    allow_human: bool = False,
    allow_fail: bool = False,
) -> Path:
    project = project.resolve()
    series = load_series(project)
    plan = read_json(episode_plan_path(project, episode))
    if no_freeze:
        document = read_json(manifest_path(project, episode))
    else:
        document = freeze(project, episode, allow_human=allow_human, allow_fail=allow_fail)
    errors = verify(project, document)
    if errors:
        raise AssembleError("refusing to build:\n" + "\n".join(errors))
    issues = static_checks(project, plan)
    if issues:
        raise AssembleError("audio/line check failed:\n" + "\n".join(issues))
    _attach_line_durations(project, plan)
    delivery = document.get("delivery") or {}
    width = int(delivery.get("width") or series.delivery.width)
    height = int(delivery.get("height") or series.delivery.height)
    fps = int(delivery.get("fps") or series.delivery.fps)
    cuts = plan.get("cuts") or []
    total = float(cuts[-1]["at"]) + float(cuts[-1]["dur"])
    takes = {row["shot_id"]: row for row in document["takes"]}
    font = find_font(bold=True)
    paths = output_paths(project, episode)
    paths["mp4"].parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="satrcut-asm-") as tmp_name:
        tmp = Path(tmp_name)
        segments: list[Path] = []
        for index, cut in enumerate(cuts):
            dest = tmp / f"seg_{index:02d}.mp4"
            if cut.get("source") == "card:time":
                _encode_segment(cut, None, dest, width, height, fps, font)
            else:
                row = takes[_shot_id(cut)]
                local = dict(cut)
                if row.get("verdict") == "TRIM" and row.get("trim"):
                    local["_trim"] = row["trim"]
                _encode_segment(local, project / row["file"], dest, width, height, fps, font)
            segments.append(dest)
        raw = tmp / "raw.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for segment in segments:
            cmd += ["-i", str(segment)]
        graph = "".join(f"[{index}:v:0]" for index in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[v]"
        require_ok(cmd + ["-filter_complex", graph, "-map", "[v]", "-an", "-c:v", "libx264", "-preset", x264_preset(), "-crf", "20", "-r", str(fps), str(raw)])
        mix = tmp / "mix.wav"
        audio_filter = mix_audio(project, plan, total, mix)
        write_ass(plan, paths["ass"], width, height)
        write_srt(plan, paths["srt"])
        ass_filter = f"ass='{ffmpeg_filter_path(paths['ass'])}':fontsdir='{ffmpeg_filter_path(font.parent)}'"
        require_ok(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(raw), "-i", str(mix),
                "-vf", ass_filter,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", x264_preset(), "-crf", "20",
                "-c:a", "aac", "-b:a", "160k",
                "-t", f"{total:.3f}",
                "-movflags", "+faststart",
                str(paths["mp4"]),
            ]
        )
    thumb_at = float(plan.get("stakes_at_s") or 1.0)
    label = f"{series.name}  ep {episode:02d}".replace(":", r"\:").replace("'", "")
    require_ok(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{thumb_at:.3f}", "-i", str(paths["mp4"]),
            "-frames:v", "1",
            "-vf",
            f"scale=540:960,drawtext=fontfile='{ffmpeg_filter_path(font)}':text='{label}':fontsize=28:fontcolor=white:borderw=2:bordercolor=black:x=(w-tw)/2:y=h-70",
            str(paths["jpg"]),
        ]
    )
    width_out, height_out = video_size(paths["mp4"])
    report = {
        "file": paths["mp4"].relative_to(project).as_posix(),
        "sha256": sha256_file(paths["mp4"]),
        "duration_s": round(media_duration(paths["mp4"]), 3),
        "width": width_out,
        "height": height_out,
        "thumbnail": paths["jpg"].relative_to(project).as_posix(),
        "srt": paths["srt"].relative_to(project).as_posix(),
        "ass": paths["ass"].relative_to(project).as_posix(),
        "audio_filter": audio_filter,
        "shippable": True,
        "built": now_iso(),
    }
    atomic_write_json(paths["report"], report)
    return paths["mp4"]
