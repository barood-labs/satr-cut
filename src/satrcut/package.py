"""Episode packaging: a title card, a poster, and a trailer of hook shots.

Art is drawn here. It does not use another company's logos, footage, or type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from satrcut.project import Series, episode_plan_path, load_series
from satrcut.util import find_font, read_json, require_ok


def _font(size: int):
    try:
        from PIL import ImageFont

        return ImageFont.truetype(str(find_font(bold=True)), size)
    except (OSError, RuntimeError):
        from PIL import ImageFont

        return ImageFont.load_default()


def _canvas(title: str, subtitle: str, kicker: str) -> Image.Image:
    image = Image.new("RGB", (1080, 1920), (28, 18, 16))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1080, 28), fill=(245, 208, 40))
    draw.rectangle((80, 1500, 1000, 1512), fill=(245, 208, 40))
    draw.text((80, 620), kicker, fill=(245, 208, 40), font=_font(36))
    draw.text((80, 700), title, fill=(255, 248, 240), font=_font(84))
    draw.text((80, 1540), subtitle, fill=(230, 214, 200), font=_font(40))
    return image


def title_card(series: Series, episode: int, plan: dict[str, Any], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    title = str(plan.get("title") or f"Episode {episode}")
    image = _canvas(title, series.name, f"Episode {episode}")
    image.save(dest)
    return dest


def poster(series: Series, plan: dict[str, Any], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    trope = str(plan.get("trope") or "a vertical series")
    image = _canvas(series.name, trope.replace("-", " "), "A vertical series")
    image.save(dest)
    return dest


def _hook_sources(project: Path) -> list[tuple[str, Path | None]]:
    found = []
    folder = project / "episodes"
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("ep*.plan.json")):
        plan = read_json(path)
        for cut in plan.get("cuts") or []:
            if cut.get("beat") != "hook":
                continue
            shot_id = str(cut.get("shot_id") or str(cut.get("source") or "").split(":")[-1])
            episode = int(plan.get("episode") or 1)
            shot_dir = project / "renders" / f"ep{episode:02d}" / shot_id
            mp4 = next(shot_dir.glob("**/take_*.mp4"), None) if shot_dir.is_dir() else None
            if mp4 is None and shot_dir.is_dir():
                mp4 = next(shot_dir.glob("**/*.mp4"), None)
            found.append((shot_id, mp4 if mp4 and mp4.is_file() else None))
            break
    return found


def trailer(project: Path, dest: Path) -> Path:
    """Cut the hook of each episode. Missing renders become a labeled slate."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    hooks = _hook_sources(project)
    if not hooks:
        hooks = [("1-hook", None)]
    font = find_font(bold=True)
    font_path = font.as_posix().replace(":", r"\:").replace("'", r"\'")
    segments: list[Path] = []
    work = dest.parent / "_trailer"
    work.mkdir(parents=True, exist_ok=True)
    for index, (shot_id, source) in enumerate(hooks):
        segment = work / f"hook_{index:02d}.mp4"
        if source is not None:
            require_ok(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(source), "-t", "2.8", "-an",
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=24",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
                    str(segment),
                ]
            )
        else:
            label = shot_id.replace(":", " ").replace("'", "")
            require_ok(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=0x1C1210:s=1080x1920:r=24:d=2.8",
                    "-vf",
                    f"drawtext=fontfile='{font_path}':text='{label}':fontsize=72:fontcolor=0xF5D028:x=(w-tw)/2:y=(h-th)/2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
                    str(segment),
                ]
            )
        segments.append(segment)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for segment in segments:
        cmd += ["-i", str(segment)]
    graph = "".join(f"[{i}:v:0]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[v]"
    require_ok(cmd + ["-filter_complex", graph, "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dest)])
    return dest


def package_episode(project: Path, episode: int = 1) -> dict[str, str]:
    series = load_series(project)
    plan = read_json(episode_plan_path(project, episode))
    folder = project / "deliverables"
    title = title_card(series, episode, plan, folder / f"ep{episode:02d}_title.png")
    art = poster(series, plan, folder / "poster.png")
    thumb = folder / f"ep{episode:02d}_thumb.jpg"
    Image.open(art).resize((540, 960)).save(thumb, quality=90)
    film = trailer(project, folder / "trailer.mp4")
    return {
        "title_card": str(title.relative_to(project)),
        "poster": str(art.relative_to(project)),
        "thumbnail": str(thumb.relative_to(project)),
        "trailer": str(film.relative_to(project)),
    }
