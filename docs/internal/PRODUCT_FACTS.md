# Product Facts

This repository has two different kinds of public claims:

- Code-derived facts: detector counts, default vs deep-scan split, full graph parser languages
- Messaging choices: which numbers we lead with on the homepage, pricing, docs, and editor surfaces

## Canonical Rules

- Detector counts come from `repotoire-cli/src/detectors/mod.rs`
- Full graph language support comes from `repotoire-cli/src/parsers/mod.rs`
- Regex-scanned languages are currently `Ruby`, `PHP`, `Kotlin`, and `Swift`
- Top-level marketing should lead with `110 detectors` and `9 graph-native languages`
- Detailed support tables may also mention `13 languages total` when explicitly split into:
  - `9` full graph/tree-sitter languages
  - `4` regex-scanned languages

## Source Of Truth

Run:

```bash
python3 scripts/product_facts.py
# or
make facts
```

This updates:

- [`product-facts.json`](/home/zach/personal/repotoire/repotoire/product-facts.json)
- [`repotoire/web/src/lib/product-facts.generated.ts`](/home/zach/personal/repotoire/repotoire/repotoire/web/src/lib/product-facts.generated.ts)

Validate in CI or before release:

```bash
python3 scripts/product_facts.py --check
# or
make facts-check
```

## Migration Guidance

- Web and app surfaces should import `product-facts.generated.ts` instead of hardcoding counts.
- Markdown cannot import generated constants, so README-style docs must be updated by running the generator and then syncing wording manually.
- When in doubt, prefer explicit wording over a bare number. `9 graph-native languages` is clearer than `13 languages`.
