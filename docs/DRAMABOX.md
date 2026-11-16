# DramaBox-style preset

`style: dramabox` is the default. The craft rules live in [STYLE_RULES.md](STYLE_RULES.md). They are production rules, not a copy of any show's plot, dialogue, logo, or footage. The example series is still the original rooftop hook with Alex, Jordan, and Sam.

## Pacing

`satrcut write` builds a 60–90 second episode:

- A hook of 2.5–3 seconds, then a time card.
- Picture cuts of 1.5–2.5 seconds, mostly close-ups, with a creep or drift on every shot.
- An emotional reversal on the cut nearest the midpoint.
- A cliffhanger on an uncompleted action, or a reveal on a paywall episode, then a **Next episode** stinger card.

A paywall cliffhanger lands every 6 episodes (inside the 5–8 episode band): episodes 6, 12, 18, and so on.

## Look

Assembly uses a 1080×1920 grade: warmer skin, a little extra contrast and saturation, and a slight bloom. Subtitles are bold yellow with a thick black outline, sitting in the lower safe area (about 500px above the bottom edge) so they clear platform buttons. Loudness for this preset is -14 LUFS.

```bash
satrcut assemble -e 1 --preset dramabox
```

When the plan's style is `dramabox`, that preset is already the default.

## Story templates

Original beat templates, not scripts from any released show:

| `--trope` | Shape |
|---|---|
| `revenge` | Something was taken. The last line does not finish the taking-back. |
| `billionaire` | A hidden name. The glass has a name on it. The sentence stops. |
| `contract` | Terms on the table. Someone has already signed. |
| `second-chance` | A return. The ask is to say it again. |
| `werewolf` | Eyes change. The choice is to run or to stay. Fantasy, not a copied novel. |

```bash
satrcut write -e 1 --provider mock --trope contract
```

## Packaging

```bash
satrcut package -e 1
```

Writes a title card, a series poster, a thumbnail, and `deliverables/trailer.mp4`. The trailer is the hook shot of each episode. If a hook has not been rendered, the trailer uses a labeled slate instead of borrowing anyone else's picture.

## Score

```bash
satrcut score -e 1
```

Exit 0 when the episode matches the preset. Exit 1 lists the misses: hook timing, average shot length, cliffhanger, next-episode stinger, midpoint reversal, subtitle size and outline and safe-area margin, and loudness when `deliverables/ep01.mp4` exists.
