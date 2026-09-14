# Agent Regression Debugger plugin

Self-contained Python 3.10+ capture, replay, comparison and regression CI. The vendor SDK is generated from the repository SDK by `scripts/package_release.py`.

```bash
python plugins/agent-replay/scripts/replay.py demo --directory .replay/demo
python plugins/agent-replay/scripts/replay.py inspect .replay/traces/TRACE.json
python plugins/agent-replay/scripts/replay.py test tests/agent_cases --project agent-replay.json
```

See the root README and docs/implementation.md. Original single-step replay commands remain supported. Recorded playback and fixture application never establish application correctness by themselves.
