# Open-book ablation: is measured diagnosis real?

**Question (reviewer Finding 06, "done when"):** the prompt hands the agent a
hazard-id list and a deterministic candidate-hypotheses lookup. Is the reported
diagnosis accuracy a real capability, or is the model reading the answer off
the prompt? If diagnosis is open-book, the diagnosis–action gap is an artifact.

**Method.** Re-run all 25 base cases × 7 models in **closed-book** mode
(`render_system/render_user(..., closed_book=True)`): the safety-knowledge
block and the hypotheses lookup are withheld, so the agent must name the
active hazard from the evidence and its own process knowledge. Diagnosis is
scored exactly as in the open-book run. Sequential (concurrency 1, the Protea
gateway cap). Reproduce from `results/openbook_ablation/`.

## Result

| condition | diagnosis accuracy | admissibility |
|---|---|---|
| open-book (hazard ids + hypotheses shown) | 96/119 = **80.7 %** | 122/175 = 69.7 % |
| closed-book (both withheld) | 94/117 = **80.3 %** | 116/175 = 66.3 % |
| **Δ** | **−0.3 pts** | −3.4 pts |

**Diagnosis is not open-book.** Withholding the hazard-id list and the
hypothesis lookup moves diagnosis by 0.3 points — within noise. The models
genuinely identify the active hazard from the evidence; they are not copying a
label from the prompt. Admissibility drops slightly (−3.4 pts), consistent with
the hints giving a minor scaffold for the *action* but not the *diagnosis*.

**The gap is robust.** Because diagnosis holds while admissibility falls, the
paired gap does not shrink under closed-book — P(inadmissible | diagnosis
correct) is comparable or higher per model (e.g. protea-1 33 %→50 %,
gpt-5.5 33 %→50 %). The central claim — models diagnose correctly yet propose
inadmissible actions, concentrated at the physical-consequence gate — is not
an artifact of open-book hints.
