# Measured controlled results

Executed locally on 2026-10-17 with `agent-replay planning demo --tasks 20 --seed 0`.
20 identical evaluation task definitions per mode; 60 separate historical collection
tasks; no real model calls, model tokens, production tools or external operations.
The initial run does not include report-rendering time in task latency.

| Mode | Pass / total | Mean real tool calls | Mean task latency | Synthetic tool cost/task |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 5 / 20 | 1 | 19.90 ms | $0.020 |
| Planning, tests-only mock | 10 / 20 | 2 | 159.79 ms | $0.030 |
| Planning + historical retrieval | 11 / 20 | 3 | 332.66 ms | $0.038 |

Planning improved 5 tasks and regressed none against baseline in this sample.
Historical retrieval improved 7 tasks and **regressed 1**, for a net gain of 6.
These are observed synthetic environment outcomes. The policies and hidden
requirements are controlled fixtures, not evidence about real coding-agent success.
Latency includes capture/planning instrumentation and depends on the host.
The USD amounts are synthetic tool charges, not a measured provider bill.

Offline predictors were evaluated on the **same real observed deployment
transitions**, not on each model's preferred imagined futures. Brier score changed
from 0.5000 to 0.2885 over 20 paired task groups. The paired 95% bootstrap delta
interval was approximately [-0.4459, +0.0436], so the decision is **INCONCLUSIVE**.
No model is promoted by this demo. Calibration promotion has a separate automated
held-out positive/negative test; it remains an offline artifact operation.

The example quality gate returns **FAIL**, exit 1, for both cost and real-tool-call
increases above 25%. Increasing success rate does not erase the budget regression.
The 8-task smoke case remains **INCONCLUSIVE** under a 20-task minimum. This makes
uncertainty and tradeoffs visible instead of assigning a blanket planning win.

## Verification commands

```sh
python -m unittest discover -s tests -v
npm test
npm run test:saas
npm run test:ui
python scripts/generate_schemas.py
python scripts/package_release.py
python scripts/package_release.py --check
```

CI regenerates the full experiment and uploads its machine-readable evidence,
trajectories, gate decision and JSON/HTML/JUnit reports. Source includes one recorded
synthetic failure in `dist/planning-demo.json` for the existing detail view tests.
It contains hypotheses and actual observations separately. Run the experiment to
produce current timings and the complete comparison; do not use these fixed timings
as a performance SLA.

## Known limits

Only the deterministic in-memory deployment environment has an execution adapter.
Mock and historical predictors are validated offline; live LLM quality/cost and
stochastic-environment benefits are unverified. Histories are structured public
state, not a universal environment simulator. Canonical action deduplication does
not infer semantic equivalence. Dataset grouping relies on producer-defined task
groups; exact-context checks cannot detect all semantic near duplicates. Bootstrap
intervals are exploratory with finite samples, not a reliability guarantee.

Local final verification: **131/131 Python**, **11/11 web unit**, **13/13 SaaS**,
and **2/2 browser workflows** passed. Browser workflows include registration,
existing seven product views, planning inspection and viewer/XSS checks. The wheel
built successfully; packaged SDK/web/plugin sources and deterministic source ZIP
passed the distribution drift check. Live provider calls were mocked in tests.
