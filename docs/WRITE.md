# satrcut write

`satrcut write` turns a series bible into a season arc, episode beats, and a shot plan that already passes `satrcut lint`.

```bash
satrcut write -e 1 --provider mock --premise "A secret told in close-ups."
satrcut write -e 1 --episodes 3 --provider mock
```

The chain is:

1. Season arc (`season.json`): one emotional beat and an uncompleted-action cliffhanger per episode.
2. Episode beat sheet (`episodes/epNN.yaml`): hook, time card, close-ups, one stakes line, a last line.
3. Shot plan (`episodes/epNN.plan.json`): prompts from the house style, 4n+1 frame counts, locked wardrobe.
4. Auto-fix: if lint still reports an error, the shot's action and prompt are rebuilt and lint runs again, up to six passes.

Rules the writer enforces:

- The first picture cut is a hook of at most 3 seconds (and at least 2.5, so the linter's hook window holds).
- Other picture cuts are 1.5–2.5 seconds.
- The ending is `uncompleted_action`, and the last cut ends on a line.
- Every episode has an emotional beat: rupture, confession, threat, reversal, or longing.

## LLM providers

| `--provider` | Network | Key |
|---|---|---|
| `mock` (default) | none | none |
| `openai` | `SATRCUT_LLM_BASE_URL` (default `https://api.openai.com/v1`) | `SATRCUT_LLM_API_KEY` |
| `ollama` | `SATRCUT_OLLAMA_URL` (default `http://127.0.0.1:11434`) | none |

The key is sent only as an Authorization header. It is not copied into the plan, the season file, or the error text. If a model returns something lint cannot repair, the writer falls back to the deterministic sheet so the saved plan still lints. CI uses `mock` only.
