# satrcut studio

A local review page. It binds to `127.0.0.1` only.

```bash
satrcut studio
# http://127.0.0.1:8765
satrcut studio --port 9000
```

The page shows:

- The episode timeline (each cut's length and framing).
- A card per shot: line, QC verdict, failure reasons, seed, attempt.
- Approve, reject, and retake.
- The render queue (shots with no finished manifest, or a partial).
- GPU-hours and a dollar estimate.

Approve writes `human_accept` on a PASS, TRIM, or REVIEW manifest. A failed take cannot be approved; retake it. Retake deletes that shot's manifest so the next `satrcut render` does the shot again. Reject records the decision and leaves the file.

Cost is `sum(render seconds) / 3600 * cost.gpu_usd_per_hour` from `series.yaml`. The default rate is 0. The page says the number is not a charge. Studio never calls a billing API.

```yaml
cost:
  gpu_usd_per_hour: 0.40
```
