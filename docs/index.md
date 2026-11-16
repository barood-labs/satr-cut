# Satr Cut

Satr Cut is a production CLI for serialized vertical micro-dramas. The shot plan is the contract. Lint runs before the GPU. Renders resume after a reboot. Assembly is one checksummed 1080×1920 file.

Version 0.2 adds a writer, a consistency registry, a local review studio, more providers, dialogue routing, and export presets. The default path still uses the mock provider: no GPU and no API keys.

```bash
satrcut init demo-hook && cd demo-hook
satrcut write -e 1 --provider mock
satrcut lint -e 1
satrcut render -e 1 --provider mock
satrcut assemble -e 1
satrcut studio
```

The pages here are the same files as the repository `docs/` directory.

- [Plan format](PLAN_FORMAT.md)
- [Write](WRITE.md)
- [Providers](PROVIDERS.md)
- [Studio](STUDIO.md)
- [Export and subtitles](EXPORT.md)
- [Failure taxonomy](FAILURE_TAXONOMY.md)
- [Why shots fail](ROOT_CAUSES.md)
- [Style rules](STYLE_RULES.md)
- [Architecture](ARCHITECTURE.md)
