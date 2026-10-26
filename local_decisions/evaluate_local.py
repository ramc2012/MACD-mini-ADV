"""Resumable frozen held-out verdict scoring; never deploys model weights.

Scores are conditional token likelihoods, not calibrated win probabilities.
Every prediction is persisted before outcome metrics are computed.
"""
import argparse
from datetime import datetime, time
import hashlib
import json
import os
from pathlib import Path
import threading
from zoneinfo import ZoneInfo


def metrics(rows, predictions):
    if len(rows) != len(predictions):
        raise ValueError('Incomplete predictions cannot produce full-split metrics')
    approved = [r for r, p in zip(rows, predictions) if p['verdict'] == 'approve']
    skipped = [r for r, p in zip(rows, predictions) if p['verdict'] != 'approve']
    def summary(items):
        profit = sum(max(r['net_return'], 0) for r in items)
        loss = sum(min(r['net_return'], 0) for r in items)
        return {'rows': len(items), 'mean_net_return': sum(r['net_return'] for r in items)/len(items) if items else None,
                'sum_net_return_units': profit+loss, 'loss_units': -loss, 'profit_units': profit,
                'profit_factor': profit/-loss if loss else None, 'wins': sum(r['net_return'] > 0 for r in items),
                'mean_stress_return': sum(r['stress_return'] for r in items)/len(items) if items else None}
    return {'all': summary(rows), 'approved': summary(approved), 'skipped': summary(skipped),
            'approval_fraction': len(approved)/len(rows) if rows else 0,
            'warning': 'Equal-notional closed-trade diagnostic, not a cash-constrained portfolio backtest or prospective edge'}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


def evaluate(base, adapter, corpus, output, resume=False):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    def allowed():
        if time(8,30) <= datetime.now(ZoneInfo('Asia/Kolkata')).time() < time(16):
            raise RuntimeError('Evaluation held during market hours')
    allowed()
    def deadline():
        import time as clock
        while True:
            try: allowed()
            except RuntimeError: os._exit(75)
            clock.sleep(5)
    threading.Thread(target=deadline, daemon=True).start()
    from mlx_lm import utils
    utils.maybe_set_recommended_wired_limit = lambda: None
    from local_decisions.mlx_compat import enable_global_scaled_linear
    from local_decisions.verdict_head import verdict_logits
    enable_global_scaled_linear()
    import mlx.core as mx
    from mlx_lm import load
    from mlx_lm.tuner.utils import load_adapters
    mx.set_memory_limit(9*1024**3); mx.set_cache_limit(128*1024**2)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=resume)
    receipt = {'base': str(Path(base).resolve()), 'adapter': str(Path(adapter).resolve()),
               'corpus': str(Path(corpus).resolve()), 'method': 'verdict-head-v2-fixed-argmax',
               'base_files': {p.name: digest(p) for p in sorted(Path(base).iterdir()) if p.suffix in ('.json', '.safetensors')},
               'adapter_sha256': digest(Path(adapter)/'adapters.safetensors'),
               'prompts': {s: digest(Path(corpus)/(s+'.jsonl')) for s in ('valid', 'test')},
               'records': {s: digest(Path(corpus)/(s+'.records.jsonl')) for s in ('valid', 'test')}}
    receipt_path = output/'receipt.json'
    if receipt_path.exists():
        if json.loads(receipt_path.read_text()) != receipt:
            raise ValueError('Evaluation provenance changed; choose a new output directory')
    else:
        atomic_json(receipt_path, receipt)
    model, tokenizer = load(base, lazy=True); model.eval()
    labels = ('approve', 'reject', 'wait')
    ids = [tokenizer.encode(s, add_special_tokens=False) for s in labels]
    if not all(len(v) == 1 for v in ids): raise ValueError('Verdicts must each be one token')
    token_ids = mx.array([v[0] for v in ids])
    records = {s: [json.loads(line) for line in (Path(corpus)/(s+'.records.jsonl')).read_text().splitlines()] for s in ('valid', 'test')}
    frozen = {s: [json.loads(line)['messages'][:-1] for line in (Path(corpus)/(s+'.jsonl')).read_text().splitlines()] for s in records}
    if any(len(records[s]) != len(frozen[s]) for s in records): raise ValueError('Prompt/record counts differ')
    corpus_manifest=json.loads((Path(corpus).parent/'manifest.json').read_text())
    result = {'method': receipt['method'], 'live_promotion_allowed': False, 'complete': False,
              'quality_tier':corpus_manifest.get('quality_tier','recorded_paper_close'),
              'quality_warning':'Historical selection and exit-policy limitations apply; reconstructed data additionally lacks historical availability/L1 proof',
              'adapter_sha256': receipt['adapter_sha256'], 'evaluations': {}}
    for stage in ('base', 'adapter'):
        if stage == 'adapter': load_adapters(model, adapter); model.eval()
        result['evaluations'][stage] = {}
        for split, rows in records.items():
            path = output/(stage+'-'+split+'.predictions.jsonl')
            predictions = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            if [p['id'] for p in predictions] != [r['id'] for r in rows[:len(predictions)]] or len(predictions) > len(rows):
                raise ValueError('Saved prediction identities differ')
            with path.open('a') as file:
                for i in range(len(predictions), len(rows)):
                    allowed()
                    prefix = tokenizer.apply_chat_template(frozen[split][i], tokenize=False, add_generation_prompt=True, enable_thinking=False)
                    prefix += '{"reviews":[{"id":"candidate","verdict":"'
                    tokens = mx.array([tokenizer.encode(prefix, add_special_tokens=False)])
                    hidden = model.language_model.model(tokens)[:, -1]
                    logits = verdict_logits(model.language_model, hidden, token_ids)[0].astype(mx.float32)
                    values = mx.softmax(logits); mx.eval(values)
                    if not bool(mx.all(mx.isfinite(values)).item()): raise ValueError('Nonfinite verdict scores')
                    scores = values.tolist()
                    prediction = {'id': rows[i]['id'], 'verdict': labels[max(range(3), key=lambda j: scores[j])],
                                  'conditional_token_scores': dict(zip(labels, scores))}
                    file.write(json.dumps(prediction, allow_nan=False)+'\n'); file.flush(); os.fsync(file.fileno())
                    predictions.append(prediction)
                    del hidden, logits, values, tokens; mx.clear_cache()
                    if (i+1) % 25 == 0: print(stage, split, i+1, '/', len(rows), flush=True)
            result['evaluations'][stage][split] = metrics(rows, predictions)
            atomic_json(output/'report.json', result)
    result['complete'] = True
    atomic_json(output/'report.json', result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    from local_decisions.compute_guard import exclusive_compute
    p = argparse.ArgumentParser()
    for name in ('base', 'adapter', 'corpus', 'output'): p.add_argument('--'+name, required=True)
    p.add_argument('--resume', action='store_true'); a = p.parse_args()
    with exclusive_compute():evaluate(a.base, a.adapter, a.corpus, a.output, a.resume)
