"""Use inference kernels in frozen Qwen layers; keep adapted layers trainable."""


def frozen_prefix_eval(model,num_layers):
    if not 1<=num_layers<=len(model.layers):raise ValueError('Invalid adapted-layer count')
    base=type(model)
    class MixedModeModel(base):
        def train(self,mode=True):
            result=super().train(mode)
            for layer in self.layers[:-num_layers]:layer.eval()
            return result
    model.__class__=MixedModeModel
    return model
