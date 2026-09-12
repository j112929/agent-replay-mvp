"""Set OPENAI_API_KEY, optional OPENAI_BASE_URL, and REPLAY_MODEL first."""
import os
from agent_replay import capture

with capture("Model comparison") as run:
    response = run.chat(model=os.environ["REPLAY_MODEL"], messages=[{"role": "user", "content": "A payment lookup returned HTTP 429. Describe a safe recovery plan in three sentences."}], max_completion_tokens=256)
    print(response["choices"][0]["message"])
print(run.path)
