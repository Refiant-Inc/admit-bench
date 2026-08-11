# Related work

*Where ADMIT Bench sits among the benchmarks, frameworks, and standards it
draws on — and the specific thing it does that none of them do: put a
deterministic admissibility verdict in the path of an industrial agent's
action, before any score exists.*

The field has produced three broad kinds of artifact. **Benchmarks** measure
what agents or models can do — execute procedures, recall standards, diagnose
faults, automate maintenance workflows. **Frameworks and systems** make agents
act — on control loops, alarms, simulations, digital twins. **Standards and
domain literature** define what safe operation means for humans. ADMIT Bench
is the piece between them: it treats every agent output as a proposed state
transition over a physical system and asks the question none of the
benchmarks ask and all of the standards imply — *was this action admissible?*

## 1 · Benchmarks

| benchmark | asks | unit of evaluation | scoring basis | safety treatment | relation to ADMIT Bench |
|---|---|---|---|---|---|
| SOP-Bench (arXiv:2506.08119) | can agents execute industrial SOPs? | task from an expert-authored SOP with executable tool interface (2,000+ tasks, 12 domains) | task success rate, tool-call accuracy against ground-truth outputs | none — procedural competence only | the workflow layer ADMIT Bench sits upstream of: completing the SOP is T3's *subject*, not the verdict; a completed procedure can still be an inadmissible intervention |
| IndustryBench (arXiv:2605.10267) | does industrial knowledge survive a standards check? | QA item graded against national standards (2,049 items, 10 industries) | 0–3 rubric plus a separate safety-violation rate on the answer text | post-hoc textual check; safety violations reshuffle rankings | shares the core finding that correctness is not enough — and moves it to text; ADMIT Bench moves it to *actions*, with the check in the path rather than after the answer |
| AssetOpsBench [23] | can agents automate asset operations and maintenance? | scenario over asset data, work orders, and tools | task completion across multi-agent workflows | not the organizing axis | the maintenance-planning cousin: rich tooling, no physics-gated intervention; its scenarios would compile naturally into cartridges |
| PHMForge [21] · PHM-Bench [22] | can models do prognostics and health management? | diagnostic/prognostic query or MCP tool task | accuracy against labeled faults | none | diagnosis is one tier of one gate here (T2): naming the fault never authorizes the action, and a missed fault only vetoes when it changes the action class |
| FaultExplainer [24] | can models explain detected faults interpretably? | fault explanation on the Tennessee Eastman process | explanation quality/accuracy | none | the audit-layer cousin: in ADMIT Bench, rationale is recorded on every trace and never scored — explanation cannot buy back an inadmissible action |
| Tennessee Eastman problem [11] | can a control policy run a plant? | control policy on the simulated plant | control performance; shutdown constraints implicit | constraints in the simulation itself | the ancestor of the consequence verifier: physics as ground truth, three decades before LLM agents; ADMIT Bench keeps that discipline and adds the record around the action |
| MMLU · HumanEval [30, 31] | is the answer correct? | answer or program against a key | accuracy, pass@k | none | the innermost of the three nested questions — a property of the output; ADMIT Bench's outermost question contains it and needs no answer key on live work |

Two structural differences separate ADMIT Bench from every row above. First,
**the unit**: not a task, an answer, or an explanation, but a trace-backed
proposed state transition — the action plus the evidence, authority,
procedure, reversibility, and recovery that must justify it. Second, **the
place of judgment**: benchmark verdicts happen beside the action path and
gate nothing; the admissibility check is a component the action must pass
through, and a failed hard gate yields no aggregate at all rather than a low
one. The litmus pair makes the difference operational: a correct answer with
a skipped required step must fail; a justified escalation must pass. Every
benchmark above grades those two episodes the other way, or cannot express
them.

## 2 · Frameworks, systems, and the domain canon

| family | representative work | what it contributes | how ADMIT Bench relates |
|---|---|---|---|
| agentic industrial control | Vyas & Mercangöz [1]; González-Potes et al. [2]; Liang et al. [3] | LLM agents that observe, decide, and act on process systems | these are the actors ADMIT Bench exists to judge: the gate is designed to sit between exactly such frameworks and the plant, and their action proposals are already record-shaped |
| plant model and engineering artifact generation | Kunze & Fay [4] | LLM-drafted plant models (AML) from documents | the same trust posture as the cartridge studio: model-drafted artifacts are candidates that must compile and be reviewed before anything runs against them |
| alarm-facing LLM systems | Dalzell et al. [5] | LLM triage over SCADA alarm streams | alarm-derived evidence is exactly what T1 treats as degradable; the alarm-flood scenario family (CE-110) encodes why triage must never substitute for base physics |
| alarm management discipline | EEMUA 191 [6]; ISA-18.2 [7]; Stauffer & Clarke [8]; Stauffer & Sands [9]; Akintoye & Harrow [10] | alarm lifecycle, rationalization, alarms as protection layers, safety response time | imported directly: freshness windows, quality flags, the response-time clock that decides when escalation becomes an unsafe delay (T4), and alarm floods as first-class evidence failure |
| root-cause analysis | Gharahbagheri et al. [12]; Noroozifar & Izadi [13]; Shahid et al. [14]; Javanbakht et al. [15] | data-driven RCA over process and alarm data | the hypothesis lookup is deliberately the boring version — deterministic lexical pointers into a reviewed cause→effect graph, surfaced as hypotheses to verify and kept out of scoring entirely |
| digital twins | Rebello & Nogueira [16]; Viola & Chen [17]; CLOI [18]; Rozyyev et al. [19]; Liu, Celik et al. [20] | high-fidelity plant twins, online learning, uncertainty assessment | the consequence verifier is a small, deliberately transparent twin; the twin literature's fidelity and uncertainty agenda is precisely the verifier-authority gap this bench declares open (STANDARD §9) |
| neuro-symbolic verification | Galitsky et al. [25] | symbolic checks preventing LLM hallucination in control | the same instinct, generalized: one deterministic checker over typed records, running identically at rehearsal, gate, and audit |
| human factors | Endsley [26] | situation awareness as the operator's real resource | the evidence feed and leakage discipline are situation-awareness design: the agent sees what an operator would see, at the level the episode grants, and nothing more |
| functional safety | IEC 61511 [27] | independent protection layers, safety instrumented systems | the reason `disable_interlock` is forbidden rather than expensive, and the source of the series-not-average gate architecture |
| process models | Henson & Seborg [28]; Skogestad [29] | canonical CSTR and distillation dynamics | the structural basis of the two shipped worlds; kept small and inspectable on purpose |

## 3 · References

As provided for the working draft; arXiv identifiers for [15], [21], [22],
[23], [24] and the venue details for [20] should be confirmed against the
originals prior to any external submission.

- **[S1]** SOP-Bench: Benchmarking LLM Agents on Industrial Standard Operating
  Procedures. arXiv:2506.08119, 2025.
- **[S2]** IndustryBench: Probing the Industrial Knowledge Boundaries of LLMs.
  arXiv:2605.10267, 2026.
- [1] J. Vyas and M. Mercangöz. Autonomous Industrial Control using an Agentic
  Framework with Large Language Models. IFAC-PapersOnLine, 59(6):349–354,
  2025. DOI: 10.1016/j.ifacol.2025.07.170. arXiv:2411.05904.
- [2] A. González-Potes et al. Hybrid AI and LLM-Enabled Agent-Based Real-Time
  Decision Support Architecture for Industrial Batch Processes. AI, 7(2):51,
  2026.
- [3] J. Liang, N. Groll, and G. Sin. Large Language Model Agent for
  User-friendly Chemical Process Simulations. arXiv:2601.11650, 2026.
- [4] F.C. Kunze and A. Fay. Automated Generation of AML Models for Industrial
  Plants Using LLM Chat Applications. IEEE ETFA, 2024.
- [5] G. Dalzell et al. Enhancing SCADA Alarm Management for Power Grids using
  Large Language Models. IECON, 2024.
- [6] EEMUA. Publication 191: Alarm Systems — A Guide to Design, Management
  and Procurement. 4th edn. EEMUA, 2024.
- [7] ISA. ANSI/ISA-18.2-2016: Management of Alarm Systems for the Process
  Industries. ISA, 2016.
- [8] T. Stauffer and P. Clarke. Using Alarms as a Layer of Protection.
  Process Safety Progress, 35(1):76–83, 2016. DOI: 10.1002/prs.11739.
- [9] T. Stauffer and N.P. Sands. Get a Life(cycle)! Connecting Alarm
  Management and Safety Instrumented Systems. exida technical paper.
- [10] A. Akintoye and S. Harrow. Determination of Alarm Safety Response Time.
  IChemE Symposium Series No. 161, Hazards 26, 2016.
- [11] J.J. Downs and E.F. Vogel. A Plant-Wide Industrial Process Control
  Problem. Computers & Chemical Engineering, 17(3):245–255, 1993.
- [12] H. Gharahbagheri, S. Imtiaz, and A. Khan. Root Cause Diagnosis of
  Process Fault Using KPCA and Bayesian Network. I&EC Research,
  56(8):2054–2070, 2017.
- [13] A. Noroozifar and I. Izadi. Root Cause Analysis of Process Faults Using
  Alarm Data. ICEE, 1118–1122, 2019. DOI: 10.1109/IranianCEE.2019.8786718.
- [14] M. Shahid et al. Fault Root Cause Analysis Using Degree of Change and
  Mean Variable Threshold Limit in Non-linear Dynamic Distillation Column.
  Process Safety and Environmental Protection, 189:856–866, 2024.
  DOI: 10.1016/j.psep.2024.07.001.
- [15] N. Javanbakht, A. Neshastegaran, and I. Izadi. Alarm-Based Root Cause
  Analysis in Industrial Processes Using Deep Learning. arXiv:2203.11321,
  2022.
- [16] R. Rebello and I. Nogueira. Digital Twins in Chemical Engineering: An
  Integrated Framework for Identification, Implementation, Online Learning,
  and Uncertainty Assessment, 2025.
- [17] J. Viola and Y. Chen. Digital Twins for Industrial Applications:
  Modeling, Simulation, and Real-Time Monitoring, 2020.
- [18] CLOI: A Benchmark Framework for Geometric Digital Twins of Industrial
  Facilities. arXiv:2101.01355, 2021.
- [19] A. Rozyyev et al. Design and Evaluation of an AI-Based Digital Twin for
  Industrial Manufacturing Optimization. SPIE Proceedings, 2026.
- [20] Y.-Y. Liu, N. Celik et al. Supercharging Digital Twins with AI.
  Asia-Pacific Journal of Operational Research, 2025. (Venue to be
  confirmed.)
- [21] T. Feng et al. PHMForge: Evaluating LLM Agents on Industrial
  Prognostics through MCP-Native Tools. arXiv:2604.01532, 2026.
- [22] PHM-Bench: A Domain-Specific Benchmarking Framework for LLMs in
  Prognostics and Health Management. arXiv:2508.02490, 2025.
- [23] AssetOpsBench: Benchmarking AI Agents for Task Automation in Industrial
  Asset Operations and Maintenance. arXiv:2506.03828, 2025.
- [24] FaultExplainer: Leveraging Large Language Models for Interpretable
  Fault Detection and Diagnosis. arXiv:2412.14492, 2024.
- [25] B. Galitsky et al. Neuro-Symbolic Verification for Preventing LLM
  Hallucinations in Process Control. Processes, 14(2):322, 2026.
- [26] M.R. Endsley. Toward a Theory of Situation Awareness in Dynamic
  Systems. Human Factors, 37(1):32–64, 1995.
- [27] IEC 61511-1:2016. Functional Safety — Safety Instrumented Systems for
  the Process Industry Sector. IEC, 2016.
- [28] M.A. Henson and D.E. Seborg (eds.). Nonlinear Process Control.
  Prentice Hall, 1997.
- [29] S. Skogestad. Dynamics and Control of Distillation Columns: A Critical
  Survey. Modeling, Identification and Control, 18:177–217, 1997.
- [30] D. Hendrycks et al. Measuring Massive Multitask Language Understanding.
  ICLR, 2021.
- [31] M. Chen et al. Evaluating Large Language Models Trained on Code.
  arXiv:2107.03374, 2021.

The process-safety and incident literature grounding the scenario content
(van Heerden, Aris–Amundson, Stoessel, Kister, Fair, API 521, CSB and HSE
investigations) is registered separately in
[CE_LIBRARY.md §7](CE_LIBRARY.md), where each scenario family cites its
sources directly.
