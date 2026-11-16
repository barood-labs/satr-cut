# Examples

`demo-hook/` is the project `satrcut init` writes: a 10-cut rooftop hook, about 23 seconds, placeholder cast (Alex, Jordan, Sam). It is not a full 90-second episode and it is not from a private series.

```bash
cd examples/demo-hook
satrcut plan generate -e 1
satrcut lint -e 1
satrcut render -e 1 --provider mock
satrcut assemble -e 1
```

The mp4 lands in `deliverables/ep01.mp4` (1080×1920), which is gitignored, along with the wavs, keyframes, and the generated plan. The YAML in this folder is the source.
