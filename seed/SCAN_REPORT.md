# Secret and story scan report

**When:** 16 November 2026, 4:04 PM MT (America/Denver, UTC-7)
**Source (read-only):** the in-house production tool directory and its root-cause notes.
The show bible, footage, and host paths were not copied.
**Source was not modified.**

## Tools

- Regex search over candidate `.py` and the two docs, then again over the seed
  (paths, `token`, `api_key`, `password`, `Bearer`, `HF_TOKEN`, `sk-`, `hf_`,
  private key armor, AWS key IDs, emails).
- [gitleaks](https://github.com/gitleaks/gitleaks) 8.28.0, `detect --no-git`
  on the seed: **no leaks found** (0 findings, ~260 KB scanned).
- trufflehog was not installed; gitleaks plus regex was used instead.

## What was copied

| Path | Action |
|---|---|
| `v2/shotlint.py`, `qc_gate.py`, `keyframes.py`, `keyframe_ops.py`, `assemble_v2.py`, `runner_v2.py` | Copied, scrubbed |
| `v3/assemble_v5.py`, `calibrate_motion.py`, `gate_v3.py`, `motion_gate.py`, `runner_v3.py`, `runner_v3b.py`, `shotlint_v5.py`, `style.py` | Copied, scrubbed |
| `v3/build_ep02_plan.py`, `build_motion_plan.py`, `build_v3_shots.py`, `build_v5_plan.py` | **Not copied.** Replaced with stubs. Story, dialogue and locations from the in-house series are not in the seed. Public replacement: `v3/build_example_plan.py` + `examples/` |
| `pipeline/*.py` (13 files, no `*.bak*`) | Copied, scrubbed |
| `production/ROOT_CAUSES.md`, `STYLE_RULES_from_reference.md` | Rewritten as generalized docs (`docs/ROOT_CAUSES.md`, `docs/STYLE_RULES.md`). Character names, show title, and local media paths removed. Craft rules kept. |
| `*.bak*`, `inputs/`, footage, voice refs, ONNX models, QC reports, job outputs, season bible, screenplays | **Skipped** |

## Findings (source, before scrub)

No API keys, passwords, bearer tokens, private keys, or Hugging Face tokens
were present in the candidate files.

Movies had flagged the word `token` in `pipeline/common.py`, `wan_comfy.py`
and `lipsync_musetalk.py`. Inspection:

- `common.py` `hf_download` builds a public `https://huggingface.co/.../resolve/...` URL and pulls with aria2c or curl. **No Authorization header, no `HF_TOKEN`, no cookie.**
- `lipsync_musetalk.py` lists the `huggingface_hub` package and calls `hf_download`. Same, no credential.
- `wan_comfy.py` uses `headers={"Content-Type": "application/json"}` against `http://127.0.0.1:<port>` (local ComfyUI). Not a secret.
- Model `sha256` pins in `wan_comfy.py` are public weight checksums, not credentials. Kept.

Machine paths that **were** present and have been stripped or replaced:

- Personal Windows user paths on the GPU host → `SATRCUT_ROOT` (default `~/satrcut`)
- The in-house project directory → `SATRCUT_PROJECT` or cwd
- A temporary Linux work directory → `./work`
- The old internal env prefix → `SATRCUT_*`
- The old internal product prefix → `satrcut`
- A named maintenance-window flag → `MAINT_WINDOW` (scheduled GPU hand-off)
- Doc references to a personal uploads path and the in-house show title

Story text removed or generalized: character names, wardrobe lock for the
in-house cast, episode plan builders, hook/ending dialogue from that series.
The demo plan uses placeholder characters Alex / Jordan / Sam and a throwaway
rooftop hook.

One series-only lint rule, L25, banned a hardcoded list of Arabic given names.
That is a per-show constraint, not a product default. In the seed it is an
optional `forbidden_names` list on the plan, empty by default.

## Residual / accepted

- `127.0.0.1` ComfyUI URLs — localhost, kept.
- The ASS subtitle format has a drop-shadow column. That is a format keyword, not a host or a person.
- `examples/audio/lines/*.wav` — short **silent** PCM placeholders so shotlint's L04 path check is quiet. Not voice references from the series.
- In-house shot ids in comments inside `shotlint.py` (`1-01b t01`, etc.) name historical lint failures, not plot. Left as citations.
- A later human pass is still required on the machine that makes the first public commit, before `git push`.

## Tarball

Built next to this folder as `satr-cut-seed.tar.gz`. Target: under 20 MB, no
models, no footage, no `__pycache__`.
