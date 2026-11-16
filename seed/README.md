# Satr Cut seed

Scrubbed algorithms from an in-house DramaBox-style vertical short-drama
pipeline. This is a source seed for `github.com/barood-labs/satr-cut`, not the
published repo yet and not the original series.

The supported interface is the `satrcut` package in this repository.
`seed/` is the scrubbed algorithm source those modules were refactored from.
See `docs/` for the plan format, providers, and failure taxonomy.

## What is in here

- `v2/` shotlint, QC gate, keyframes, assemble, crash-resume runner
- `v3/` later runner, assemble, gate, style, plus `build_example_plan.py`
- `pipeline/` ComfyUI Wan I2V helpers and (failed) lip-sync experiments
- `docs/ROOT_CAUSES.md`, `docs/STYLE_RULES.md`
- `examples/` a 10-cut demo plan that **lints clean** (`shotlint` 0 errors, 0 warnings when `--root examples`)

Story-specific episode builders were replaced with stubs. Machine paths and
the old internal product prefix were stripped. Secrets: see `SCAN_REPORT.md`.

## Quick check (no GPU, no keys)

```bash
python3 v2/shotlint.py examples/demo_ep01_plan.json --episode 1 --root examples
python3 v3/build_example_plan.py
```

Do not point this seed at a personal GPU host path. Use `SATRCUT_ROOT` and
`SATRCUT_PROJECT`.
