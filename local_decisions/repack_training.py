"""Requantize unsupported global-scaled NVFP4 layers in a derived training copy.

Ollama's source tensors remain untouched. The candidate must be evaluated against
its own repacked, untrained baseline; adapters cannot be attached to the original.
"""
import argparse
import gc
import json
from pathlib import Path


def repack(source,destination):
    import mlx.core as mx
    mx.set_default_device(mx.cpu)
    source=Path(source);dest=Path(destination);dest.mkdir(parents=True,exist_ok=False)
    config=json.loads((source/'config.json').read_text())
    config['quantization']={'group_size':32,'bits':4,'mode':'affine'}
    (dest/'config.json').write_text(json.dumps(config,indent=2))
    audit=[]
    for p in sorted(source.glob('model*.safetensors')):
        values=mx.load(p);globals=[k for k in values if k.endswith('.global_scale')]
        mxfp8=[k[:-len('.scales')] for k,v in values.items() if k.endswith('.scales') and k[:-len('.scales')]+'.global_scale' not in values and v.dtype==mx.uint8]
        target=dest/p.name
        if not globals and not mxfp8:target.symlink_to(p.resolve())
        else:
            for key in globals:
                prefix=key[:-len('.global_scale')]
                # Ollama stores the dequant multiplier, while MLX's optional
                # global_scale argument denotes an amax and divides by 2688.
                dense=mx.dequantize(values[prefix+'.weight'],values[prefix+'.scales'],group_size=16,bits=4,mode='nvfp4',dtype=mx.bfloat16)*values[key].astype(mx.bfloat16)
                weight,scales,biases=mx.quantize(dense,group_size=32,bits=4,mode='affine')
                restored=mx.dequantize(weight,scales,biases,group_size=32,bits=4)
                err=mx.mean(mx.abs(restored-dense))/mx.maximum(mx.mean(mx.abs(dense)),1e-8)
                mx.eval(weight,scales,biases,err)
                audit.append({'tensor':prefix,'relative_mean_absolute_error':float(err.item())})
                values[prefix+'.weight']=weight;values[prefix+'.scales']=scales;values[prefix+'.biases']=biases;del values[key]
            for prefix in mxfp8:
                dense=mx.dequantize(values[prefix+'.weight'],values[prefix+'.scales'],group_size=32,bits=8,mode='mxfp8',dtype=mx.bfloat16)
                weight,scales,biases=mx.quantize(dense,group_size=64,bits=8,mode='affine')
                restored=mx.dequantize(weight,scales,biases,group_size=64,bits=8)
                err=mx.mean(mx.abs(restored-dense))/mx.maximum(mx.mean(mx.abs(dense)),1e-8)
                mx.eval(weight,scales,biases,err)
                audit.append({'tensor':prefix,'source_mode':'mxfp8','target_bits':8,'relative_mean_absolute_error':float(err.item())})
                values[prefix+'.weight']=weight;values[prefix+'.scales']=scales;values[prefix+'.biases']=biases
            mx.save_safetensors(str(target),values)
        del values;gc.collect();mx.clear_cache()
    for p in source.iterdir():
        if p.name!='config.json' and p.suffix!='.safetensors':(dest/p.name).symlink_to(p.resolve())
    (dest/'repack_receipt.json').write_text(json.dumps({'purpose':'Research QLoRA compatibility only','source':str(source),'network_used':False,'layers':audit},indent=2))
    print('Repacked derived layers',len(audit),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('destination');a=p.parse_args();repack(a.source,a.destination)
