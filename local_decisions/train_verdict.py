"""LoRA fine-tuning of the constrained approve/reject/wait decision itself.

Only three frozen vocabulary rows are projected, avoiding optimization of JSON
boilerplate. Label tokens are removed from backbone inputs before the forward pass.
"""
from datetime import datetime,time
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from zoneinfo import ZoneInfo


def verdict_loss(model,batch,lengths,token_ids):
    import mlx.core as mx
    import mlx.nn as nn
    rows=mx.arange(batch.shape[0]);prefix=lengths[:,0]
    inputs=batch[:,:-1];steps=mx.arange(inputs.shape[1])[None,:]
    inputs=mx.where(steps<prefix[:,None],inputs,0)
    language=model.language_model
    hidden=language.model(inputs)[rows,prefix-1]
    from local_decisions.verdict_head import verdict_logits
    logits=verdict_logits(language,hidden,token_ids)
    matches=batch[rows,prefix][:,None]==token_ids[None,:]
    targets=mx.argmax(matches,axis=1)
    loss=nn.losses.cross_entropy(logits,targets,reduction='mean')
    return mx.where(mx.all(matches.sum(axis=1)==1),loss,mx.array(float('nan'))),mx.array(batch.shape[0])


def run(config):
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['TOKENIZERS_PARALLELISM']='false'
    now=datetime.now(ZoneInfo('Asia/Kolkata'))
    if time(8,30)<=now.time()<time(16):raise RuntimeError('Training is held during market hours')
    def deadline():
        import time as clock
        while True:
            if time(8,30)<=datetime.now(ZoneInfo('Asia/Kolkata')).time()<time(16):os._exit(75)
            clock.sleep(5)
    threading.Thread(target=deadline,daemon=True).start()
    from mlx_lm import utils
    utils.maybe_set_recommended_wired_limit=lambda:None
    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx_lm import load
    from mlx_lm.tuner.trainer import train,TrainingArgs
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from mlx_lm.tuner.utils import load_adapters
    from local_decisions.mixed_mode import frozen_prefix_eval
    import numpy as np
    from local_decisions.mlx_compat import enable_global_scaled_linear
    enable_global_scaled_linear();mx.set_memory_limit(9*1024**3);mx.set_cache_limit(128*1024**2)
    c=json.loads(Path(config).read_text());folder=Path(c['data'])
    manifest=json.loads((folder.parent/'manifest.json').read_text())
    if manifest.get('quality_tier')=='reconstructed_unverified_availability' and not c.get('allow_reconstructed_research'):
        raise ValueError('Reconstructed examples require an explicit research-only opt-in')
    counts=manifest['lanes'][folder.name]
    if min(counts['train']['positive'],counts['train']['negative'])<3 or counts['train']['rows']<25:
        raise ValueError('Insufficient mixed-label research observations')
    mx.random.seed(c['seed']);np.random.seed(c['seed'])
    model,tokenizer=load(c['model'],lazy=True);model.freeze()
    if c.get('resume_adapter_path'):load_adapters(model,c['resume_adapter_path'])
    else:linear_to_lora_layers(model,c['num_layers'],c['lora_parameters'])
    frozen_prefix_eval(model,c['num_layers'])
    ids=[tokenizer.encode(s,add_special_tokens=False) for s in ('approve','reject','wait')]
    if not all(len(v)==1 for v in ids):raise ValueError('Verdict labels must each be one token')
    token_ids=mx.array([v[0] for v in ids]);classes={'approve':0,'reject':1,'wait':2}
    def dataset(name):
        result=[]
        for line in (folder/(name+'.jsonl')).read_text().splitlines():
            messages=json.loads(line)['messages'];target=json.loads(messages[-1]['content'])['reviews'][0]['verdict']
            text=tokenizer.apply_chat_template(messages[:-1],tokenize=False,add_generation_prompt=True,enable_thinking=False)
            tokens=tokenizer.encode(text+'{"reviews":[{"id":"candidate","verdict":"',add_special_tokens=False)
            result.append((tokens+[ids[classes[target]][0]],len(tokens)))
        return result
    train_data=dataset('train');valid_data=dataset('valid')
    adapter=Path(c['adapter_path']);adapter.mkdir(parents=True,exist_ok=False)
    c['fine_tune_type']='lora';c['objective']='constrained_verdict_cross_entropy';c['train_examples']=len(train_data)
    (adapter/'adapter_config.json').write_text(json.dumps(c,indent=2))
    context=max(len(r[0]) for r in train_data+valid_data)+32
    args=TrainingArgs(batch_size=c['batch_size'],iters=c['iters'],val_batches=min(5,len(valid_data)//c['batch_size']),
        steps_per_report=10,steps_per_eval=40,steps_per_save=40,adapter_file=adapter/'adapters.safetensors',
        max_seq_length=context,grad_accumulation_steps=c['grad_accumulation_steps'])
    mx.random.seed(c['seed'])
    print('Constrained-verdict training',folder.name,'rows',len(train_data),'iterations',c['iters'],flush=True)
    train(model=model,optimizer=optim.Adam(learning_rate=c['learning_rate']),args=args,
          train_dataset=train_data,val_dataset=valid_data,loss=lambda m,b,l:verdict_loss(m,b,l,token_ids))
    weights=mx.load(str(adapter/'adapters.safetensors'))
    if not all(bool(mx.all(mx.isfinite(v)).item()) for v in weights.values()):raise ValueError('Nonfinite adapter')
    receipt={'complete':True,'classification_only':True,'live_promotion_allowed':False,
             'at':datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(),
             'config':c,'adapter_sha256':hashlib.sha256((adapter/'adapters.safetensors').read_bytes()).hexdigest(),
             'prompts_sha256':{s:hashlib.sha256((folder/(s+'.jsonl')).read_bytes()).hexdigest() for s in ('train','valid','test')}}
    (adapter/'training_receipt.json').write_text(json.dumps(receipt,indent=2))
    print('Saved finite verdict adapter',adapter,flush=True)


if __name__=='__main__':
    from local_decisions.compute_guard import exclusive_compute
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args()
    with exclusive_compute():run(a.config)
