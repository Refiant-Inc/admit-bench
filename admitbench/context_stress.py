"""Context-length stress: the same decision, buried in a longer shift.

Industrial evidence does not arrive curated. This lane pads a case's evidence
feed with deterministic, plausible, *irrelevant* plant noise — other units'
readings, routine maintenance chatter, shift-handover notes — and reruns the
episode at growing context sizes. Two numbers fall out per size: does the
verdict hold, and does the model still cite the load-bearing evidence
(retrieval recall under noise)?

The padding is platform-attested like any evidence, but deliberately: from
trusted channels, about equipment that is not in this cartridge's system
graph, with no tags, and worded to avoid this cartridge's hazard vocabulary —
so it can neither trip the coaching lint, shift the hypothesis lookup toward
a wrong family, nor change any gate's ground truth. The answer key is
untouched; only the haystack grows.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Optional

CHARS_PER_TOKEN = 4  # coarse but sufficient for sizing tiers

_NOISE_TEMPLATES = [
    ("historian", "TK-{n} tank farm level {lvl}% and steady for the shift"),
    ("historian", "P-{n} lube oil header at {p} kPa, within band"),
    ("historian", "unit {u} export meter totalizer advanced {t} m3 this hour"),
    ("control_system", "controller LIC-{n} on unit {u} in AUTO, output {o}%, no deviation"),
    ("control_system", "analyzer shelter {u} HVAC running, cabinet temp {c} C"),
    ("maintenance_log", "work order WO-{w}: routine lubrication round on unit {u} completed, no findings"),
    ("maintenance_log", "scaffold inspection tag renewed at unit {u} pipe rack bay {n}"),
    ("maintenance_log", "instrument air dryer tower swap on schedule; dewpoint normal"),
    ("operator_chat", "shift note: canteen closed early, night meal moved to 01:30"),
    ("operator_chat", "unit {u} board: nothing to report on the hourly round"),
    ("historian", "tower basin {n} makeup pump cycled {v} times, routine duty"),
    ("historian", "flare pilot {n} confirmed lit on camera, steady"),
]


def make_noise_evidence(
    target_chars: int,
    seed: int,
    before_s: float,
    start_index: int = 0,
) -> list[dict]:
    """Deterministic filler evidence totalling roughly target_chars of content."""
    rng = random.Random(seed)
    entries: list[dict] = []
    total = 0
    i = start_index
    while total < target_chars:
        source, template = _NOISE_TEMPLATES[rng.randrange(len(_NOISE_TEMPLATES))]
        content = template.format(
            n=rng.randint(1, 9) * 100 + rng.randint(1, 99),
            u=rng.choice(["U-200", "U-300", "U-700", "U-900"]),
            lvl=rng.randint(35, 85),
            p=rng.randint(300, 700),
            t=rng.randint(40, 400),
            o=rng.randint(20, 80),
            c=rng.randint(19, 27),
            w=rng.randint(10000, 99999),
            v=rng.randint(2, 9),
        )
        entries.append(
            {
                "id": f"ev_noise_{i:04d}",
                "content": content,
                "source": source,
                "received_at": round(rng.uniform(0.0, max(before_s - 5.0, 1.0)), 1),
            }
        )
        total += len(content) + 60  # plus the per-entry rendering overhead
        i += 1
    return entries


def pad_case(cartridge, case, target_tokens: int, seed: int = 7):
    """A deep copy of the case with noise interleaved into its evidence feed.

    Original evidence, load-bearing ids, and every answer-key field are
    untouched; noise ids are disjoint by construction (`ev_noise_*`).
    """
    padded = copy.deepcopy(case)
    existing_chars = sum(len(e.get("content", "")) for e in padded.evidence)
    target_chars = max(target_tokens * CHARS_PER_TOKEN - existing_chars, 0)
    noise = make_noise_evidence(target_chars, seed=seed, before_s=padded.decided_at())
    padded.evidence = sorted(
        padded.evidence + noise, key=lambda e: float(e.get("received_at", 0.0))
    )
    return padded, [e["id"] for e in noise]


@dataclass
class ContextPoint:
    case_id: str
    target_tokens: int
    verdict: str
    aas_code: Optional[str]
    action: Optional[str]
    load_bearing_recall: Optional[float]
    noise_citations: int
    prompt_chars: int
    latency_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "target_tokens": self.target_tokens,
            "verdict": self.verdict,
            "aas_code": self.aas_code,
            "action": self.action,
            "load_bearing_recall": self.load_bearing_recall,
            "noise_citations": self.noise_citations,
            "prompt_chars": self.prompt_chars,
            "latency_s": self.latency_s,
        }


def run_context_point(cartridge, case, provider, target_tokens: int, seed: int = 7) -> ContextPoint:
    from admitbench.runner import run_episode

    padded, noise_ids = pad_case(cartridge, case, target_tokens, seed=seed)
    result = run_episode(cartridge, padded, provider)

    load_bearing = set(case.load_bearing or [])
    cited = set(result.record.cited_evidence) if result.record else set()
    recall = (
        round(len(cited & load_bearing) / len(load_bearing), 4) if load_bearing else None
    )
    prompts = result.trace.get("prompts") or {}
    return ContextPoint(
        case_id=case.id,
        target_tokens=target_tokens,
        verdict=result.score.verdict,
        aas_code=result.score.aas_code,
        action=result.record.action if result.record else None,
        load_bearing_recall=recall,
        noise_citations=len(cited & set(noise_ids)),
        prompt_chars=len(prompts.get("system", "")) + len(prompts.get("user", "")),
        latency_s=(result.trace.get("completion") or {}).get("latency_s", 0.0),
    )
