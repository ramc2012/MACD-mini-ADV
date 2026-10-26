"""Small local language/JSON sanity probe; writes no trading journal."""
import argparse
import json
from pathlib import Path


def probe(base,output):
    from local_decisions.mlx_compat import enable_global_scaled_linear
    enable_global_scaled_linear()
    import mlx.core as mx
    from mlx_lm import load,generate
    from mlx_lm.sample_utils import make_sampler
    mx.set_memory_limit(9*1024**3);mx.set_cache_limit(128*1024**2)
    model,tokenizer=load(base,lazy=True)
    prompt=tokenizer.apply_chat_template([{'role':'user','content':'Reply with the single word READY.'}],tokenize=False,add_generation_prompt=True,enable_thinking=False)
    answer=generate(model,tokenizer,prompt=prompt,max_tokens=16,sampler=make_sampler(temp=0),verbose=False)
    result={'base':base,'instruction':'Reply with the single word READY.','answer':answer,'exact_match':answer.strip()=='READY'}
    Path(output).write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    return result['exact_match']


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('base');p.add_argument('output');a=p.parse_args();probe(a.base,a.output)
