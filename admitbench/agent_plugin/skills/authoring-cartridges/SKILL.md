---
name: authoring-cartridges
description: Turn plant documentation — HAZOP rows, SOP pages, P&ID notes, operator logs — into an ADMIT cartridge, the four-file safety knowledge format that lets the admissibility gates run against a specific plant. Use when someone wants to evaluate or gate an agent on a plant that ADMIT does not already ship, or asks how to encode their own process safety knowledge for machine checking.
---

# Authoring an ADMIT cartridge

A cartridge is what makes the gates specific to *your* plant. Without one, the
tools can only reason about the two reference plants that ship with ADMIT.

The boundary that matters: authoring is creative and may use a model freely.
What comes out is deterministic and replayable. Nothing you draft reaches an
episode until it compiles.

## The four files

```
cartridges/<plant>/
  manifest.yaml            what system, whose authority, which actions, whom to trust
  system_graph.jsonl       what exists physically — assets, tags, limits
  safety_case_graph.jsonl  what can go wrong and why — hazards, cause→effect,
                           unsafe actions, recoveries (each with a review_status)
  procedures_cases.jsonl   SOPs (written once, read twice) and benchmark cases
```

## Procedure

**1. Get the authoring prompt.** It is long, versioned, and shipped with the
package — do not reconstruct it from memory:

```bash
admitbench prompt
```

**2. Draft.** Paste that prompt into any chat model along with the source
documents. Save the four files it produces under `cartridges/<id>/`.

**3. Compile.** This is the gate, and it is not advisory:

```bash
admitbench validate cartridges/<id>
```

The validator names every problem by file and entry. Fix and re-run until it
compiles. A cartridge that does not compile cannot run an episode.

**4. Check it behaves.** A cartridge can compile and still be wrong. Run the
reference agents against it — the oracle should be admissible everywhere, the
reckless agent should not be:

```bash
admitbench run --cartridge cartridges/<id> --provider stub --model oracle
admitbench run --cartridge cartridges/<id> --provider stub --model reckless
```

If `oracle` fails a case, the case is miswritten, not the agent.

## Things that go wrong

- **Knowledge asserted as fact.** Cause→effect entries carry a `review_status`
  of `candidate`, `reviewed`, or `validated`. Anything a model inferred starts
  at `candidate` and is rendered to agents clearly marked unverified. Promoting
  it is a human act (`admitbench promote`), not a drafting shortcut.
- **Limits that are round numbers.** A trip limit copied from a summary table
  rather than the safety case is the most common silent error. Trace each one.
- **Cases with no way to fail.** A case where every allowed action is
  admissible measures nothing. Each case should have a defensible response and
  at least one plausible-looking action the gates reject.
- **Authority that matches capability.** The interesting cases are the ones
  where the physically obvious fix is outside the agent's authority. Write at
  least one.

## Where the rules live

`docs/CARTRIDGE_AUTHORING.md` is the full walkthrough. `docs/CE_LIBRARY.md` has
the master cause→effect library with literature-anchored scenario families you
can adapt rather than invent.

Once it compiles, the MCP tools in this plugin accept it, and the agent gets
gated against your plant instead of a reference one.
