# Contributing

Satr Cut is MIT. Issues and pull requests are welcome.

## Setup

```bash
git clone https://github.com/barood-labs/satr-cut
cd satr-cut
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

ffmpeg must be on `PATH`. Tests do not need a GPU, a ComfyUI server, or an API key.

```bash
pytest
```

CI runs that suite on Python 3.11 and 3.12.

## What to change

The shot plan is the contract. If you add a field, document it in `docs/PLAN_FORMAT.md` and cover it with a test.

Lint rules keep their ids (`L01`, `L02`, …). The regular expressions in `src/satrcut/lint.py` match `seed/v2/shotlint.py`. A test fails if they drift. New rules get a new id and a fixture that fails for that reason.

Do not weaken a rule to make a prompt pass. Change the prompt so it describes what is in frame.

Providers implement `VideoProvider` (`healthy`, `restart`, `render`). The mock provider stays the default and stays offline. A provider that needs a key reads it from the environment at call time and never writes it into a plan, a log, or a fixture.

`seed/` is the scrubbed source of the algorithms. Prefer a small, tested change in `src/satrcut/` over editing the seed. If you do fix a seed crash, keep the behavior the package tests.

## Checks before a PR

- `pytest` is green.
- `gitleaks detect --source . --no-git` is clean. Weight sha256 pins are public checksums; they are not credentials.
- No story text, voice references, footage, or machine-specific paths from a private series.
- No new dependency that is GPL and vendored. ComfyUI stays behind HTTP.
- Lip-sync stays off. Do not claim a close-up model works without a fixture series that passes.

## Commit style

Short, imperative subject. Say which command or rule changed and why.
