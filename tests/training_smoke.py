"""Offline tiny-transformer acceptance: real GRPO/DDP, checkpoint and resume."""
import json
import os
from pathlib import Path
import subprocess
import sys
import socket
import tempfile
import torch
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast
from agent_replay.rollout.artifacts import seal_checkpoint, verify_checkpoint
from agent_replay.serialization import digest
from agent_replay.rollout.grpo import advantages, loss


def main():
    # No-op uniform reward group and masked token gradients.
    assert torch.equal(advantages(torch.ones(2,4)),torch.zeros(2,4))
    lp=torch.tensor([[-.7,-.8],[-.9,-.5]],requires_grad=True)
    obj=loss(lp,lp.detach(),torch.tensor([1.,-1.]),torch.tensor([[1.,0.],[1.,0.]]));obj.backward()
    assert torch.equal(lp.grad[:,1],torch.zeros(2))
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);initial=root/'initial';initial.mkdir()
        torch.manual_seed(12)
        tok=Tokenizer(models.WordLevel({'<pad>':0,'<eos>':1,'question':2,'4':3,'5':4,'<unk>':5},unk_token='<unk>'))
        tok.pre_tokenizer=pre_tokenizers.Whitespace()
        tokenizer=PreTrainedTokenizerFast(tokenizer_object=tok,pad_token='<pad>',eos_token='<eos>',unk_token='<unk>')
        tokenizer.save_pretrained(initial)
        model=GPT2LMHeadModel(GPT2Config(vocab_size=6,n_positions=32,n_embd=16,n_layer=1,n_head=2,resid_pdrop=0,embd_pdrop=0,attn_pdrop=0,bos_token_id=1,eos_token_id=1))
        model.save_pretrained(initial)
        reference=seal_checkpoint(initial)
        with torch.no_grad():
            probs=model(torch.tensor([[2]])).logits[0,0].log_softmax(-1)
        policy={'version':0,'weights':reference,'digest':digest(reference)}
        samples=[]
        for i in range(2):
            data={'task':{'expected_answer':'4'},'prompt_token_ids':[2],'generations':[{'text':text,'token_ids':[token],'logprobs':[float(probs[token])]} for text,token in [('4',3),('5',4)]]}
            samples.append({'id':str(i),'data':data})
        settings={'checkpoint_root':str(root),'device':'cpu','learning_rate':.005,'grpo_iterations':2,'seed':12}
        request={'policy':policy,'samples':samples,'config':settings}
        inp=root/'input.json';out=root/'output.json';inp.write_text(json.dumps(request))
        env=dict(os.environ,OMP_NUM_THREADS='1',TOKENIZERS_PARALLELISM='false')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        cmd=[sys.executable,'-m','torch.distributed.run','--master-addr=127.0.0.1','--master-port='+str(port),'--nproc_per_node=2','-m','agent_replay.rollout.torch_learner',str(inp),str(out)]
        subprocess.run(cmd,check=True,env=env,timeout=120)
        result=json.loads(out.read_text());checkpoint=verify_checkpoint(result['weights'],root)
        state=torch.load(checkpoint/'training-state.pt',weights_only=True)
        assert state['sample_ids']==['0','1'] and state['optimizer']['state']
        changed=GPT2LMHeadModel.from_pretrained(checkpoint,local_files_only=True)
        with torch.no_grad():
            after=changed(torch.tensor([[2]])).logits[0,0].softmax(-1)[3]
        assert after>probs.exp()[3],(after,probs.exp()[3])
        # Identical retry reuses the completed immutable checkpoint.
        subprocess.run(cmd,check=True,env=env,timeout=120)
        assert json.loads(out.read_text())['weights']==result['weights']
        # Next update restores optimizer and RNG from its parent checkpoint.
        request['policy']={'version':1,'weights':result['weights'],'digest':digest(result['weights'])}
        inp.write_text(json.dumps(request));subprocess.run(cmd,check=True,env=env,timeout=120)
        resumed=json.loads(out.read_text());assert resumed['weights']!=result['weights']
        print(json.dumps({'world_size':2,'backend':'gloo','initial_correct_token_probability':float(probs.exp()[3]),'after_grpo_probability':float(after),'optimizer_restored':True,'checkpoint_retry_idempotent':True}))

if __name__=='__main__':main()
