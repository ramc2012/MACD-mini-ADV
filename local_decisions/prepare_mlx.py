"""Expose the already installed Ollama MLX tensors as a local MLX-LM checkpoint.

No network, downloads, dequantization or original model changes. Quantized tensor
payload bytes are copied unchanged; only Safetensors scale key names are adapted.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct


def prepare(root,output,digest):
    root=Path(root);output=Path(output)
    manifest=json.loads((root/'blobs'/digest.replace(':','-')).read_text())
    if manifest.get('runner')!='mlx':raise ValueError('Expected installed MLX manifest')
    output.mkdir(parents=True,exist_ok=False)
    files=[]
    for layer in manifest['layers']:
        source=root/'blobs'/layer['digest'].replace(':','-')
        if source.stat().st_size!=layer['size']:raise ValueError('Model layer size mismatch')
        kind=layer['mediaType'];name=layer.get('name','')
        if kind.endswith('.json'):
            if Path(name).name!=name:raise ValueError('Invalid metadata path')
            if name=='config.json':
                config=json.loads(source.read_text());config.pop('quantization_config',None)
                config['quantization']={'group_size':16,'bits':4,'mode':'nvfp4'}
                (output/name).write_text(json.dumps(config,indent=2))
            elif name!='hf_quant_config.json':(output/name).symlink_to(source)
        elif kind.endswith('.license'):(output/'LICENSE').symlink_to(source)
        elif kind.endswith('.tensor'):
            if name.startswith(('model.visual.','vision_tower.')) or 'mtp.' in name:continue
            with source.open('rb') as f:
                size=struct.unpack('<Q',f.read(8))[0]
                if size>1000000:raise ValueError('Unbounded tensor header')
                header=json.loads(f.read(size))
                renamed={k.replace('.weight.global_scale','.global_scale').replace('.weight.scale','.scales'):v for k,v in header.items()}
                if len(renamed)!=len(header):raise ValueError('Tensor key collision')
                target=output/f'model-{len(files):05d}.safetensors'
                if header==renamed:target.symlink_to(source)
                else:
                    raw=json.dumps(renamed,separators=(',',':')).encode();raw+=b' '*((-len(raw))%8)
                    with target.open('wb') as dest:
                        dest.write(struct.pack('<Q',len(raw)));dest.write(raw);shutil.copyfileobj(f,dest,8*1024*1024)
                files.append({'name':name,'source_digest':layer['digest'],'file':target.name,'payload_changed':False})
    receipt={'base_digest':digest,'source':'Existing local Ollama weights','network_used':False,'tensors':files}
    (output/'conversion_receipt.json').write_text(json.dumps(receipt,indent=2))
    return len(files)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True);p.add_argument('--digest',required=True)
    a=p.parse_args();print('Prepared local tensor shards:',prepare(a.root,a.output,a.digest),flush=True)
