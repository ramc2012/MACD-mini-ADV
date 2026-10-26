"""Small native-MLX numerical tests; run with the isolated trainer Python."""
import importlib.util
from types import SimpleNamespace
import unittest


@unittest.skipUnless(importlib.util.find_spec('mlx'), 'Native MLX only')
class NativeTrainingTests(unittest.TestCase):
    def test_selected_head_matches_full_projection_and_gradient(self):
        import mlx.core as mx
        import mlx.nn as nn
        from local_decisions.verdict_head import verdict_logits
        mx.random.seed(52)
        head = nn.Linear(64, 17, bias=True).to_quantized(group_size=32, bits=4)
        language = SimpleNamespace(lm_head=head)
        hidden = mx.random.normal((2,64)); ids = mx.array([2,7,13])
        actual = verdict_logits(language, hidden, ids); expected = head(hidden)[:,ids]
        self.assertLess(float(mx.max(mx.abs(actual-expected)).item()), 1e-4)
        left = mx.grad(lambda x: verdict_logits(language,x,ids).sum())(hidden)
        right = mx.grad(lambda x: head(x)[:,ids].sum())(hidden)
        self.assertLess(float(mx.max(mx.abs(left-right)).item()), 1e-4)

    def test_loss_hides_target_token_and_trailing_padding(self):
        import mlx.core as mx
        import mlx.nn as nn
        from local_decisions.train_verdict import verdict_loss
        seen = []
        class Body:
            def __call__(self, inputs):
                seen.append(inputs.tolist())
                return mx.ones((*inputs.shape,64))
        language = SimpleNamespace(model=Body(), lm_head=nn.Linear(64,17).to_quantized(group_size=32,bits=4))
        model = SimpleNamespace(language_model=language)
        batch = mx.array([[8,9,2,999,999], [8,9,10,7,999]])
        lengths = mx.array([[2,3],[3,4]])
        loss, count = verdict_loss(model, batch, lengths, mx.array([2,7,13])); mx.eval(loss)
        self.assertTrue(bool(mx.isfinite(loss).item()))
        self.assertEqual(seen, [[[8,9,0,0], [8,9,10,0]]])
        self.assertEqual(count.item(), 2)

    def test_only_adapted_layer_uses_training_mode(self):
        import mlx.nn as nn
        from local_decisions.mixed_mode import frozen_prefix_eval
        class Model(nn.Module):
            def __init__(self): super().__init__(); self.layers = [nn.Linear(2,2) for _ in range(4)]
        model = frozen_prefix_eval(Model(),1)
        model.train(); self.assertEqual([layer.training for layer in model.layers], [False,False,False,True])
        model.eval(); self.assertFalse(any(layer.training for layer in model.layers))

    def test_nvfp4_global_multiplier_applies_once(self):
        import mlx.core as mx
        import mlx.nn as nn
        from local_decisions.mlx_compat import enable_global_scaled_linear
        enable_global_scaled_linear()
        mx.random.seed(53)
        layer = nn.Linear(64,4,bias=True).to_quantized(group_size=16,bits=4,mode='nvfp4',global_scale=True)
        layer.global_scale = mx.array(.125)
        x = mx.random.normal((2,64))
        reference = nn.QuantizedLinear.__call__(layer,x)
        expected = (reference-layer.bias)*.125+layer.bias
        self.assertLess(float(mx.max(mx.abs(layer(x)-expected)).item()), 1e-5)


if __name__ == '__main__': unittest.main()
