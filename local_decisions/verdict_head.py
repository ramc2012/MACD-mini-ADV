"""Project only the three fixed verdict rows of the frozen language head."""


def verdict_logits(language, hidden, token_ids):
    import mlx.core as mx

    head = language.lm_head
    weights = mx.dequantize(
        head.weight[token_ids], head.scales[token_ids],
        head.biases[token_ids] if head.get('biases') is not None else None,
        group_size=head.group_size, bits=head.bits, mode=head.mode,
    )
    logits = hidden @ weights.T
    if head.get('bias') is not None:
        logits += head.bias[token_ids]
    return logits
