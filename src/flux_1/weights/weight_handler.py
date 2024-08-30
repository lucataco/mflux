from pathlib import Path

import mlx.core as mx
from huggingface_hub import snapshot_download
from mlx.utils import tree_unflatten
from safetensors import safe_open

from flux_1.config.config import Config


class WeightHandler:

    def __init__(
            self,
            repo_id: str | None = None,
            local_path: str | None = None,
    ):
        root_path = Path(local_path) if local_path else WeightHandler._download_or_get_cached_weights(repo_id)

        self.clip_encoder, _ = WeightHandler._clip_encoder(root_path=root_path)
        self.t5_encoder, _ = WeightHandler._t5_encoder(root_path=root_path)
        self.vae, _ = WeightHandler._vae(root_path=root_path)
        self.transformer, self.quantization_level = WeightHandler._transformer(root_path=root_path)

    @staticmethod
    def _clip_encoder(root_path: Path) -> (dict, int):
        weights, quantization_level = WeightHandler._get_weights("text_encoder", root_path)
        return weights, quantization_level

    @staticmethod
    def _t5_encoder(root_path: Path) -> (dict, int):
        weights, quantization_level = WeightHandler._get_weights("text_encoder_2", root_path)

        # Quantized weights (i.e. ones exported from this project) don't need any post-processing.
        if quantization_level is not None:
            return weights, quantization_level

        # Reshape and process the huggingface weights
        weights["final_layer_norm"] = weights["encoder"]["final_layer_norm"]
        for block in weights["encoder"]["block"]:
            attention = block["layer"][0]
            ff = block["layer"][1]
            block.pop("layer")
            block["attention"] = attention
            block["ff"] = ff

        weights["t5_blocks"] = weights["encoder"]["block"]

        # Only the first layer has the weights for "relative_attention_bias", we duplicate them here to keep code simple
        relative_attention_bias = weights["t5_blocks"][0]["attention"]["SelfAttention"]["relative_attention_bias"]
        for block in weights["t5_blocks"][1:]:
            block["attention"]["SelfAttention"]["relative_attention_bias"] = relative_attention_bias

        weights.pop("encoder")
        return weights, quantization_level

    @staticmethod
    def _transformer(root_path: Path) -> (dict, int):
        weights, quantization_level = WeightHandler._get_weights("transformer", root_path)

        # Quantized weights (i.e. ones exported from this project) don't need any post-processing.
        if quantization_level is not None:
            return weights, quantization_level

        # Reshape and process the huggingface weights
        for block in weights["transformer_blocks"]:
            block["ff"] = {
                "linear1": block["ff"]["net"][0]["proj"],
                "linear2": block["ff"]["net"][2]
            }
            if block.get("ff_context") is not None:
                block["ff_context"] = {
                    "linear1": block["ff_context"]["net"][0]["proj"],
                    "linear2": block["ff_context"]["net"][2]
                }
        return weights, quantization_level

    @staticmethod
    def _vae(root_path: Path) -> (dict, int):
        weights, quantization_level = WeightHandler._get_weights("vae", root_path)

        # Quantized weights (i.e. ones exported from this project) don't need any post-processing.
        if quantization_level is not None:
            return weights, quantization_level

        # Reshape and process the huggingface weights
        weights['decoder']['conv_in'] = {'conv2d': weights['decoder']['conv_in']}
        weights['decoder']['conv_out'] = {'conv2d': weights['decoder']['conv_out']}
        weights['decoder']['conv_norm_out'] = {'norm': weights['decoder']['conv_norm_out']}
        weights['encoder']['conv_in'] = {'conv2d': weights['encoder']['conv_in']}
        weights['encoder']['conv_out'] = {'conv2d': weights['encoder']['conv_out']}
        weights['encoder']['conv_norm_out'] = {'norm': weights['encoder']['conv_norm_out']}
        return weights, quantization_level

    @staticmethod
    def _get_weights(model_name: str, root_path: Path) -> (dict, int):
        weights = []
        quantization_level = None
        for file in sorted(root_path.glob(model_name + "/*.safetensors")):
            quantization_level = safe_open(file, framework="pt").metadata().get("quantization_level")
            weight = list(mx.load(str(file)).items())
            weights.extend(weight)

        # Non huggingface weights (i.e. ones exported from this project) don't need any reshaping.
        if quantization_level is not None:
            return tree_unflatten(weights), quantization_level

        # Huggingface weights needs to be reshaped
        weights = [WeightHandler._reshape_weights(k, v) for k, v in weights]
        weights = WeightHandler._flatten(weights)
        unflatten = tree_unflatten(weights)
        return unflatten, quantization_level

    @staticmethod
    def _flatten(params):
        return [(k, v) for p in params for (k, v) in p]

    @staticmethod
    def _reshape_weights(key, value):
        if len(value.shape) == 4:
            value = value.transpose(0, 2, 3, 1)
        value = value.reshape(-1).reshape(value.shape).astype(Config.precision)
        return [(key, value)]

    @staticmethod
    def _download_or_get_cached_weights(repo_id: str) -> Path:
        return Path(
            snapshot_download(
                repo_id=repo_id,
                allow_patterns=[
                    "text_encoder/*.safetensors",
                    "text_encoder_2/*.safetensors",
                    "transformer/*.safetensors",
                    "vae/*.safetensors",
                ]
            )
        )
