# Export presets and subtitles

```bash
satrcut assemble -e 1 --preset reelshort
satrcut assemble -e 1 --preset tiktok
satrcut assemble -e 1 --preset youtube_shorts
satrcut assemble -e 1 --preset reels
```

| Preset | Frame | fps | Loudness | Subtitle safe area |
|---|---|---|---|---|
| `master` (default) | plan delivery, usually 1080×1920 | plan | -16 LUFS | margin 420 |
| `tiktok` | 1080×1920 | 30 | -14 LUFS | higher bottom margin, extra right inset |
| `youtube_shorts` | 1080×1920 | 30 | -14 LUFS | bottom margin 480 |
| `reels` | 1080×1920 | 30 | -14 LUFS | bottom margin 540, extra right inset |
| `reelshort` | 1080×1920 | 24 | -14 LUFS | bottom margin 460 |

`-14 LUFS` is the short-video target. The master mix stays at -16. Safe-area margins keep the yellow line above the platform buttons.

## Arabic and other languages

Set `lang` on the series or the episode:

```yaml
lang: ar
```

Arabic lines are kept in logical order and wrapped with Unicode RTL embedding marks (`U+202B` … `U+202C`). libass shapes them when the font has Arabic glyphs. The subtitle style switches to **Noto Naskh Arabic**. Install that font on the machine (the Docker image installs `fonts-noto-core`). Latin lines are unchanged, so a mixed line that contains Arabic is treated as RTL.

The SRT sidecar gets the same shaping. Nothing is translated. `satrcut write` does not call a translation API.
