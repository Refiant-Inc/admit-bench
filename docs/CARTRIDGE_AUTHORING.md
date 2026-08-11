# Authoring a cartridge with any LLM

You do not need an API key wired into ADMIT Bench to build a cartridge. Any
chat model you already use — ChatGPT, Claude, anything — can do the
extraction, because the output is just four plain files and the validator
tells you exactly what to fix.

## The loop

1. **Get the prompt.**

   ```bash
   admitbench prompt > authoring_prompt.txt
   ```

   (Or copy it from `admitbench/builder.py::AUTHORING_PROMPT`.)

2. **Paste it into your LLM** and attach or paste your material: P&ID notes,
   HAZOP rows, SOPs, alarm lists, incident reports, or even a single
   paragraph describing the unit. More material means fewer `candidate`
   entries to review later.

3. **Save the four files it produces** exactly here:

   ```
   cartridges/<your_id>/manifest.yaml
   cartridges/<your_id>/system_graph.jsonl
   cartridges/<your_id>/safety_case_graph.jsonl
   cartridges/<your_id>/procedures_cases.jsonl
   ```

4. **Validate.**

   ```bash
   admitbench validate cartridges/<your_id>
   ```

   Every problem is named by file and entry ("case C03: evidence ev_x has
   source 'radio' not in trust_map"). Paste the errors back into the LLM,
   apply the fixes, revalidate. Nothing benchmarks until it compiles — that
   is the point, not an inconvenience.

5. **Run it.**

   ```bash
   admitbench run --cartridge cartridges/<your_id> --provider stub --model oracle
   ```

   The stub oracle should be admissible on every case. If it isn't, the case
   itself is inconsistent (the validator catches most of these — e.g. an
   oracle whose required evidence the case never provides).

## Review before you trust it

Everything an LLM extracts should arrive as `review_status: candidate`. The
bench renders candidates to agents as explicitly unverified and weights them
down in the hypothesis lookup. Harden knowledge one step at a time:

```bash
admitbench promote --cartridge cartridges/<your_id> --entry CE-004 --to reviewed
admitbench promote --cartridge cartridges/<your_id> --entry CE-004 --to validated
```

Operator notes join the same lifecycle without an LLM at all:

```bash
admitbench ingest --cartridge cartridges/<your_id> --log shift_notes.txt --by "night shift"
```

## The two worlds

The physics backend is selected by `world:` in the manifest — `cstr`
(temperature envelope, cooling dynamics) or `column` (pressure and level
envelopes). Pick whichever is closer to your unit's dominant hazard; the
gates, scoring, and robustness tests are world-agnostic. New worlds are one
class in `admitbench/physics.py`: state variables, `derivatives`, an
envelope, and an action→controls mapping.
