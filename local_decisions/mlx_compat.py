"""Preserve Ollama's NVFP4 per-tensor global scale in MLX-LM's frozen layers.

Only the training/evaluation process opts into this compatibility shim. Existing
weights are unchanged; the scalar multiplies the quantized matrix product.
"""


def enable_global_scaled_linear():
    import mlx.core as mx
    import mlx.nn as nn
    if getattr(nn.Linear.to_quantized,'_macd_global_scale',False):return
    original=nn.Linear.to_quantized

    class GlobalScaledLinear(nn.QuantizedLinear):
        def __call__(self,x):
            value=super().__call__(x)
            scale=self.global_scale.astype(value.dtype)
            bias=self.get('bias')
            return value*scale if bias is None else (value-bias)*scale+bias

    def convert(self,group_size=None,bits=None,mode='affine',quantize_input=False,global_scale=False):
        value=original(self,group_size=group_size,bits=bits,mode=mode,quantize_input=quantize_input)
        if global_scale:
            if mode!='nvfp4':raise ValueError('Global scale is only supported for the installed NVFP4 tensors')
            value.__class__=GlobalScaledLinear
            value.global_scale=mx.array(1.0,mx.float32)
            value.freeze()
        return value
    convert._macd_global_scale=True
    nn.Linear.to_quantized=convert
