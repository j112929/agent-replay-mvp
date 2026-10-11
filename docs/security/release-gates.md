# Release evaluation policies

Keep legacy `benchmark gate` thresholds for compatibility. Supply `--policy` to
use the new three-state release evaluator over the same benchmark comparison:

```json
{
  "environment": "PRODUCTION",
  "min_tasks": 20,
  "alpha": 0.05,
  "min_success_rate": 0.90,
  "max_success_drop": 0.02,
  "critical_tags": ["critical"],
  "max_latency_increase": 0.20,
  "max_cost_increase": 0.25,
  "require_replay": true,
  "inconclusive_action": "block"
}
```

```sh
agent-replay benchmark gate --baseline RUN_A --candidate RUN_B \
  --policy release-policy.json --output gate.json --json
agent-replay benchmark report RUN_B --baseline RUN_A --policy release-policy.json
```

Decision and release action are separate. PASS returns 0; FAIL returns 1;
blocking INCONCLUSIVE returns 3. Explicitly allowed INCONCLUSIVE returns 0 and
retains the inconclusive decision in its JSON. Invalid policy returns 2.
DEVELOPMENT defaults to allow inconclusive; STAGING and PRODUCTION default to
block. `inconclusive_action` can override that default explicitly.

Matched task definitions/IDs/verifier digests are required. Versions are copied
from AgentConfig (name/model/parameters, including caller-supplied prompt, tool and
environment version IDs). No missing version is invented. Existing structural
trajectory comparison supplies changed tools, arguments, outputs and failures.

The policy uses minimum sample size and a one-sided exact paired binomial/McNemar
test on discordant task pairs for success regression. Minimum success uses a Wilson
upper confidence bound; latency/cost regressions require a positive approximate
95% lower bound on paired threshold-adjusted differences. Descriptive success
intervals are explicitly approximate. Unknown usage/cost stays null. Missing tasks,
required reusable mock evidence, failed sanitization and infrastructure errors are
inconclusive, not passes. Critical-tag failure is a deterministic safety rule and
can fail even when the aggregate sample is small.

One run contains one observation per task. These statistics do not establish model
determinism or account for arbitrary dependence across tasks. Repeated/stochastic
trial aggregation, multiplicity correction and benchmark study design remain
future work. Do not interpret playback timing or zero replay external operations
as a new agent performance measurement. Failed model calls are not task success.

CI workflow `security-reliability.yml` proves a deliberately failing synthetic
candidate is blocked; that verification job itself should pass. Your deployment
job must depend on its own candidate gate step. This repository does not deploy an
agent release automatically, and branch protection cannot be assumed configured.
Set the gate as a required check in your repository before relying on release blocking.
