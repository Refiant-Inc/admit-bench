# Reproducing ADMIT-Bench v4

**Code:** git-rev `['09f260a']` (at run time).
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
tar xzf traces.tar.gz                 # unpacks the 5628-episode trace set
admitbench paired tmp.F7pMfvnX0n          # the paired diagnosis-action gap
admitbench dashboard tmp.F7pMfvnX0n       # gate + Layer-1 dashboards
admitbench report tmp.F7pMfvnX0n          # the three-table report
```
`results.json` holds the pre-computed tables; `episode_ledger.csv` is the
per-episode outcome ledger. Every verdict is re-derivable from its trace.

## Re-run against live models (spend applies)
```
admitbench run --cartridge cstr --provider <p> --model <m> --cmt --out runs/<name>
```
