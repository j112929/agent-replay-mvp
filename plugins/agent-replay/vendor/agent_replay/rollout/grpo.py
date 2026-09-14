"""Group-normalized, token-level clipped GRPO objective (optional reference KL)."""

def advantages(rewards, epsilon=1e-8):
    import torch
    if rewards.ndim != 2 or rewards.shape[1] < 2 or not torch.isfinite(rewards).all():
        raise ValueError('Expected finite rewards [prompts, group_size>=2]')
    centered = rewards-rewards.mean(dim=1, keepdim=True)
    return centered/(rewards.std(dim=1, unbiased=False, keepdim=True)+epsilon)


def loss(logprobs, behavior_logprobs, advantage, mask, clip=0.2, reference_logprobs=None, beta=0.0):
    import torch
    if logprobs.shape != behavior_logprobs.shape or logprobs.shape != mask.shape or advantage.shape != logprobs.shape[:1] or clip <= 0 or beta < 0:
        raise ValueError('Invalid GRPO tensor shapes or parameters')
    if not torch.isfinite(logprobs).all() or not torch.isfinite(behavior_logprobs).all() or not torch.isfinite(advantage).all() or (mask.sum(-1) <= 0).any():
        raise ValueError('Invalid probability evidence or empty completion')
    ratio = (logprobs-behavior_logprobs.detach()).exp()
    if not torch.isfinite(ratio).all():
        raise ValueError('Importance ratio overflow; reject this stale batch')
    per_token = -torch.minimum(ratio*advantage[:,None], ratio.clamp(1-clip,1+clip)*advantage[:,None])
    if beta:
        if reference_logprobs is None or reference_logprobs.shape != logprobs.shape:
            raise ValueError('Reference log probabilities required when beta > 0')
        delta = reference_logprobs.detach()-logprobs
        per_token = per_token+beta*(delta.exp()-delta-1)
    return ((per_token*mask).sum(-1)/mask.sum(-1)).mean()
