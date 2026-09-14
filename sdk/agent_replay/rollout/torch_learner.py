"""Single-node torchrun DDP GRPO learner with atomic model/optimizer/RNG checkpoints."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from .artifacts import seal_checkpoint, verify_checkpoint
from .grpo import advantages, loss
from .llm import group_rewards
from ..serialization import digest


def train(request):
    import torch
    import torch.distributed as dist
    from torch.nn.parallel import DistributedDataParallel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    settings, policy, samples = request['config'], request['policy'], request['samples']
    world, rank, local_rank = int(os.environ.get('WORLD_SIZE',1)), int(os.environ.get('RANK',0)), int(os.environ.get('LOCAL_RANK',0))
    cuda = torch.cuda.is_available() and settings.get('device','cuda') != 'cpu'
    device = torch.device('cuda',local_rank) if cuda else torch.device('cpu')
    if cuda:
        torch.cuda.set_device(device)
    if world > 1:
        dist.init_process_group('nccl' if cuda else 'gloo')
    try:
        source = verify_checkpoint(policy['weights'], settings['checkpoint_root'])
        update_key = digest({'policy': policy['digest'], 'samples': [s['id'] for s in samples], 'settings': settings})
        target = Path(settings['checkpoint_root'])/('update-'+update_key)
        if target.exists():
            reference = seal_reference(target)
            verify_checkpoint(reference, settings['checkpoint_root'])
            return {'weights': reference, 'metrics': json.loads((target/'training-metrics.json').read_text())}
        torch.manual_seed(settings.get('seed',42))
        model = AutoModelForCausalLM.from_pretrained(source, local_files_only=True, trust_remote_code=False).to(device)
        model.train()
        for module in model.modules():
            if isinstance(module, torch.nn.Dropout):
                module.p = 0.0
        tokenizer = AutoTokenizer.from_pretrained(source, local_files_only=True, trust_remote_code=False)
        pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
        optimizer = torch.optim.AdamW(model.parameters(), lr=settings.get('learning_rate',1e-6))
        state_path = source/'training-state.pt'
        if state_path.exists():
            state = torch.load(state_path, map_location='cpu', weights_only=True)
            optimizer.load_state_dict(state['optimizer'])
            torch.set_rng_state(state['rng'])
            if cuda and state.get('cuda_rng'):
                torch.cuda.set_rng_state_all(state['cuda_rng'])
        groups = []
        for sample in samples:
            trajectory = sample['data']
            rewards = group_rewards(trajectory)
            adv = advantages(torch.tensor([rewards], dtype=torch.float32))[0]
            for generation, a in zip(trajectory['generations'], adv):
                prompt = trajectory['prompt_token_ids']
                ids, old = generation['token_ids'], generation['logprobs']
                if not prompt or not ids or len(ids) != len(old):
                    raise ValueError('Incomplete token evidence')
                groups.append((prompt, ids, old, float(a)))
        if not groups or len(groups)%world:
            raise ValueError('Completion count must be divisible by DDP world size')
        local = groups[rank::world]
        length = max(len(p)+len(ids) for p,ids,_,_ in local)
        inputs = torch.full((len(local),length), pad, dtype=torch.long, device=device)
        attention = torch.zeros_like(inputs)
        old_probs = torch.zeros((len(local),length-1), device=device)
        mask = torch.zeros_like(old_probs)
        adv = torch.tensor([g[3] for g in local], device=device)
        for i,(prompt,ids,old,_) in enumerate(local):
            sequence = prompt+ids
            inputs[i,:len(sequence)] = torch.tensor(sequence, device=device)
            attention[i,:len(sequence)] = 1
            start = len(prompt)-1
            mask[i,start:start+len(ids)] = 1
            old_probs[i,start:start+len(ids)] = torch.tensor(old, device=device)
        wrapped = DistributedDataParallel(model, device_ids=[local_rank] if cuda else None) if world>1 else model
        objective = None
        for _ in range(settings.get('grpo_iterations',1)):
            optimizer.zero_grad(set_to_none=True)
            logits = wrapped(input_ids=inputs, attention_mask=attention).logits[:,:-1,:]
            logprobs = logits.float().log_softmax(-1).gather(-1, inputs[:,1:,None]).squeeze(-1)
            # Padding/non-completion locations are excluded, including from overflow checks.
            logprobs = logprobs*mask
            objective = loss(logprobs, old_probs, adv, mask, clip=settings.get('clip_epsilon',.2))
            objective.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), settings.get('max_grad_norm',1.0))
            optimizer.step()
        if objective is None:
            raise ValueError('At least one GRPO iteration required')
        mean_loss = objective.detach().clone()
        if world>1:
            dist.all_reduce(mean_loss)
            mean_loss /= world
        metrics = {'grpo_loss': float(mean_loss), 'completions': len(groups), 'world_size': world, 'updates': settings.get('grpo_iterations',1)}
        if rank == 0:
            temporary = Path(tempfile.mkdtemp(prefix='.checkpoint-',dir=settings['checkpoint_root']))
            try:
                model.save_pretrained(temporary, safe_serialization=True)
                tokenizer.save_pretrained(temporary)
                torch.save({'optimizer': optimizer.state_dict(), 'rng': torch.get_rng_state(), 'cuda_rng': torch.cuda.get_rng_state_all() if cuda else [], 'parent_policy_version': policy['version'], 'sample_ids': [s['id'] for s in samples]}, temporary/'training-state.pt')
                (temporary/'training-metrics.json').write_text(json.dumps(metrics))
                seal_checkpoint(temporary)
                os.rename(temporary,target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        if world>1:
            dist.barrier()
        return {'weights': seal_reference(target), 'metrics': metrics}
    finally:
        if world>1 and dist.is_initialized():
            dist.destroy_process_group()


def seal_reference(directory):
    manifest = json.loads((directory/'manifest.json').read_text())
    return {'format':'hf-checkpoint.v1','path':str(directory.resolve()),'manifest_digest':digest(manifest)}


if __name__ == '__main__':
    result = train(json.loads(Path(sys.argv[1]).read_text()))
    if int(os.environ.get('RANK',0)) == 0:
        Path(sys.argv[2]).write_text(json.dumps(result))
