---
name: Results submission
about: Submit an evaluation of a model for listing
title: "[results] <model>"
labels: results
---

**Model**
Exact identifier and provider (e.g. `openrouter:anthropic/claude-haiku-4.5`).

**Decoding**
Temperature, top_p, seed (if any), max tokens, and the served upstream provider
if via a router.

**Reproducibility manifest**
Paste or link the manifest from `scripts/build_artifact.py`:
- code git-rev at run:
- cartridge content-hashes:
- counts (gate base / ablations / Layer-1):
- dates:

**Headline numbers**
- Base admissibility (and range if multiple cartridges):
- Diagnosis-to-action gap (paired, if computed):
- Caution-monotonicity pass rate:
- Layer-1 unsafe selections:

**Artifact**
Link to the redacted trace bundle (no API keys). Results without a reproducible
manifest and trace bundle cannot be listed.
