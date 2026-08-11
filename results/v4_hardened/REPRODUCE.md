# Reproducing ADMIT-Bench v4

**Code:** git-rev `['03a2170']` (at run time).
**Cartridges:** content hashes in `manifest.json`.
**Decoding:** temperature 0.0, max_tokens 4000 (see `manifest.json`).
**Models:** immutable IDs and per-model date ranges in `manifest.json`.

## Recompute every number from the published traces (no API calls)

> **Trace availability:** `traces.tar.gz` (raw model outputs) is withheld from
> the public distribution pending provider terms-of-service review for
> redistributing model-generated text. Every aggregate number is still in
> `results.json` and `episode_ledger.csv` here. For research access to the raw
> traces, contact team@refiant.ai.

```
tar xzf traces.tar.gz                 # unpacks the 3283-episode trace set
admitbench paired full_eval_v4_hardened          # the paired diagnosis-action gap
admitbench dashboard full_eval_v4_hardened       # gate + Layer-1 dashboards
admitbench report full_eval_v4_hardened          # the three-table report
```
`results.json` holds the pre-computed tables; `episode_ledger.csv` is the
per-episode outcome ledger. Every verdict is re-derivable from its trace.

## Re-run against live models (spend applies)
```
admitbench run --cartridge cstr --provider <p> --model <m> --cmt --out runs/<name>
```
