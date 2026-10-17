"""Optional CPU LoRA acceptance with a tiny randomly initialized local model, no Hub."""
from pathlib import Path
import tempfile
import torch
from tokenizers import Tokenizer,models,pre_tokenizers,decoders
from transformers import GPT2Config,GPT2LMHeadModel,PreTrainedTokenizerFast
from agent_replay.rollout.artifacts import seal_checkpoint,verify_checkpoint
from agent_replay.world_model.refund import RefundAdapter,RefundBaseline,tasks
from agent_replay.benchmark.runner import run_benchmark
from agent_replay.benchmark.contracts import AgentConfig
from agent_replay.world_model.dataset import build
from agent_replay.world_model.local import train_sft,LocalHFWorldModel
from agent_replay.storage import read


def main():
    torch.set_num_threads(1)
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);base=root/'base';base.mkdir();torch.manual_seed(0)
        vocab={'<pad>':0,'<eos>':1,'<unk>':2,**{chr(i):i-29 for i in range(32,127)}}
        tok=Tokenizer(models.WordLevel(vocab,unk_token='<unk>'));tok.pre_tokenizer=pre_tokenizers.Split('',behavior='isolated');tok.decoder=decoders.Fuse()
        tokenizer=PreTrainedTokenizerFast(tokenizer_object=tok,pad_token='<pad>',eos_token='<eos>',unk_token='<unk>');tokenizer.save_pretrained(base)
        model=GPT2LMHeadModel(GPT2Config(vocab_size=len(vocab),n_positions=4096,n_embd=16,n_layer=1,n_head=2,resid_pdrop=0,embd_pdrop=0,attn_pdrop=0,eos_token_id=1,bos_token_id=1));model.save_pretrained(base,safe_serialization=True)
        reference=seal_checkpoint(base)
        run_benchmark(RefundAdapter(tasks(12)),RefundBaseline(),AgentConfig('cpu-sft-source'),root/'run')
        build([read(p) for p in (root/'run/trajectories').glob('*.json')],root/'dataset',authorized=True)
        trained=train_sft(root/'dataset',root/'sft',{'base_checkpoint':reference,'checkpoint_root':str(root),'device':'cpu','epochs':1,'max_steps':2,'max_sequence_length':4096,'lora':True,'target_modules':['c_attn'],'learning_rate':.01})
        checkpoint=trained['checkpoints'][-1];path=verify_checkpoint(checkpoint,root)
        from safetensors.torch import load_file
        weights=load_file(path/'adapter_model.safetensors')
        assert any('lora_B' in name and torch.count_nonzero(value)>0 for name,value in weights.items())
        assert trained['metrics'][0]['steps']==2 and trained['metrics'][0]['validation_loss']>0
        predictor=LocalHFWorldModel(checkpoint,root,device='cpu',max_new_tokens=8)
        assert predictor.version==checkpoint['manifest_digest']
        import asyncio
        from agent_replay.world_model.refund import RefundEnvironment,ENVIRONMENT,action
        from agent_replay.planning.contracts import AgentContext,PredictionConfig
        state=RefundEnvironment(tasks(1)[0]).snapshot()['state'];context=AgentContext('t','g','Refund',state,0,ENVIRONMENT)
        try:asyncio.run(predictor.predict(context,action('orders.lookup'),PredictionConfig()))
        except ValueError:pass  # Tiny random model is allowed to fail structured prediction; never execute output.

        print('CPU LoRA: real gradients, validation loss, sealed checkpoint and local predictor load passed. No quality or GPU claim.')

if __name__=='__main__':main()
