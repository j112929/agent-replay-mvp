"""One pinned-checkpoint vLLM engine per claimed prompt group; no mutable hot reload."""
import json
import sys
from .artifacts import verify_checkpoint


def generate(request):
    from vllm import LLM, SamplingParams
    payload, policy, settings = request['payload'], request['policy'], request['config']
    path = verify_checkpoint(policy['weights'], settings['checkpoint_root'])
    task = payload['task']
    group_size = settings.get('group_size', 4)
    if not isinstance(task.get('prompt'), str) or not task['prompt'] or type(group_size) is not int or not 2 <= group_size <= 64:
        raise ValueError('Nonempty prompt and group size 2..64 required')
    llm = LLM(model=str(path), tokenizer=str(path), trust_remote_code=False, tensor_parallel_size=settings.get('tensor_parallel_size', 1), gpu_memory_utilization=settings.get('gpu_memory_utilization', .8), max_model_len=settings.get('max_model_len', 1024), seed=payload['seed'])
    params = SamplingParams(n=group_size, temperature=1.0, top_p=1.0, max_tokens=settings.get('max_new_tokens', 64), logprobs=1, seed=payload['seed'])
    result = llm.generate([task['prompt']], params, use_tqdm=False)[0]
    generations = []
    for output in result.outputs:
        if not output.token_ids or output.logprobs is None:
            raise ValueError('vLLM must return generated token log probabilities')
        logs = [position[token].logprob for token, position in zip(output.token_ids, output.logprobs)]
        if len(logs) != len(output.token_ids):
            raise ValueError('Incomplete behavior probability evidence')
        generations.append({'text': output.text, 'token_ids': list(output.token_ids), 'logprobs': logs, 'finish_reason': output.finish_reason})
    return {'schema_version': 'rollout.v1', **{k:payload[k] for k in ('policy_version','policy_digest','environment','seed','task')}, 'prompt_token_ids': list(result.prompt_token_ids), 'generations': generations, 'generation_tokens': sum(len(g['token_ids']) for g in generations), 'transitions': [{'observation': task['prompt'], 'action': [g['text'] for g in generations], 'next_observation': None, 'behavior_logprob': sum(sum(g['logprobs']) for g in generations), 'terminated': True, 'truncated': False}]}


if __name__ == '__main__':
    from pathlib import Path
    Path(sys.argv[2]).write_text(json.dumps(generate(json.loads(Path(sys.argv[1]).read_text()))))
