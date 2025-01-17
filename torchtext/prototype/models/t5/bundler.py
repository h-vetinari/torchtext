import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Union
from urllib.parse import urljoin

import torch
from torchtext import _TEXT_BUCKET
from torchtext._download_hooks import load_state_dict_from_url

from .model import T5Conf, T5Model
from .t5_transform import T5Transform

logger = logging.getLogger(__name__)


@dataclass
class T5Bundle:
    """T5Bundle(_config: torchtext.prototype.models.T5Conf, _path: Optional[str] = None, transform: Optional[Callable] = None)

    Example - Pretrained base t5 encoder
        >>> import torch, torchtext
        >>> t5_encoder_base = torchtext.prototype.models.T5_BASE_ENCODER
        >>> transform = t5_encoder_base.transform()
        >>> input_seq = ["Hello world", "Attention rocks!"]
        >>> model = t5_encoder_base.get_model()
        >>> model_input = transform(input_seq)
        >>> output = model(model_input)['encoder_output']
        >>> output.shape
        torch.Size([2, 4, 768])

    Example - Pretrained base t5 model
        >>> import torch, torchtext
        >>> t5_base = torchtext.prototype.models.T5_BASE
        >>> transform = t5_base.transform()
        >>> input_seq = ["Hello world", "Attention rocks!"]
        >>> model = t5_base.get_model()
        >>> model_input = transform(input_seq)
        >>> output = model(model_input)['decoder_output']
        >>> output.shape
        torch.Size([2, 1, 768])

    Example - Pretrained base t5 model for generation
        >>> import torch, torchtext
        >>> import torch.nn.functional as F
        >>> t5_base_generation = torchtext.prototype.models.T5_BASE_GENERATION
        >>> transform = t5_base_generation.transform()
        >>> input_seq = ["Hello world", "Attention rocks!"]
        >>> model = t5_base_generation.get_model()
        >>> model_input = transform(input_seq)
        >>> output = model(model_input)['decoder_output']
        >>> logits = F.log_softmax(output[:,-1], dim=-1)
        >>> logits.shape
        torch.Size([2, 1, 32128])

    Example - User-specified configuration and checkpoint
        >>> from torchtext.prototype.models import T5Conf, T5Bundle
        >>> model_weights_path = "https://download.pytorch.org/models/text/t5.base.encoder.pt"
        >>> encoder_conf = T5Conf(encoder_only=True)
        >>> model = T5Bundle.build_model(config=encoder_conf, checkpoint=model_weights_path)
    """

    _config: T5Conf
    _path: Optional[str] = None
    transform: Optional[Callable] = None

    def get_model(
        self,
        *,
        load_weights: bool = True,
        freeze_model: bool = False,
        dl_kwargs: Optional[Dict[str, Any]] = None,
    ) -> T5Model:
        r"""get_model(load_weights: bool = True, freeze_model: bool = False, *, dl_kwargs=None) -> torctext.prototype.models.T5Model

        Args:
            load_weights (bool): Indicates whether or not to load weights if available. (Default: `True`)
            freeze_model (bool): Indicates whether or not to freeze the model weights. (Default: `False`)
            dl_kwargs (dictionary of keyword arguments): Passed to :func:`torch.hub.load_state_dict_from_url`. (Default: `None`)
        """

        if load_weights:
            assert (
                self._path is not None
            ), "load_weights cannot be True. The pre-trained model weights are not available for the current object"

        if freeze_model:
            if not load_weights or not self._path:
                logger.warning(
                    "The model is not loaded with pre-trained weights. Setting freeze_model to True will hinder model from learning appropriate weights."
                )

        return T5Bundle.build_model(
            config=self._config,
            freeze_model=freeze_model,
            checkpoint=self._path if load_weights else None,
            strict=True,
            dl_kwargs=dl_kwargs,
        )

    @classmethod
    def build_model(
        cls,
        config: T5Conf,
        *,
        freeze_model: bool = False,
        checkpoint: Optional[Union[str, Dict[str, torch.Tensor]]] = None,
        strict: bool = False,
        dl_kwargs: Optional[Dict[str, Any]] = None,
    ) -> T5Model:
        """Class builder method

        Args:
            config (T5Conf): An instance of classT5Conf that defined the model configuration
            freeze_model (bool): Indicates whether to freeze the model weights. (Default: `False`)
            checkpoint (str or Dict[str, torch.Tensor]): Path to or actual model state_dict. state_dict can have partial weights i.e only for encoder. (Default: ``None``)
            strict (bool): Passed to :func: `torch.nn.Module.load_state_dict` method. (Default: `False`)
            dl_kwargs (dictionary of keyword arguments): Passed to :func:`torch.hub.load_state_dict_from_url`. (Default: `None`)
        """

        model = T5Model(config, freeze_model)
        if checkpoint is not None:
            if torch.jit.isinstance(checkpoint, Dict[str, torch.Tensor]):
                state_dict = checkpoint
            elif isinstance(checkpoint, str):
                dl_kwargs = {} if dl_kwargs is None else dl_kwargs
                state_dict = load_state_dict_from_url(checkpoint, **dl_kwargs)
            else:
                raise TypeError(
                    "checkpoint must be of type `str` or `Dict[str, torch.Tensor]` but got {}".format(type(checkpoint))
                )

            model.load_state_dict(state_dict, strict=strict)

        return model

    @staticmethod
    def build_model_from_huggingface_ckpt(
        ckpt_path: Union[str, os.PathLike],
        *,
        freeze_model: bool = False,
        strict: bool = True,
    ) -> T5Model:
        """Build T5Model model from a HuggingFace checkpoint.

        Note: Only works with Huggingface models saved in the PyTorch format. Will not work with TensorFlow or JAX.

        Args:
            ckpt_path (str, Path): Path to the HF checkpoint file. Assumes that the file is local.
            freeze_model (bool): Freeze the model upon loading. (Default: `False`)
            strict (bool): Load model in strict mode. (Default: `True`)

        Returns:
            T5Model loaded with the weights of the HuggingFace checkpoint provided
        """
        config_path = f"{ckpt_path}/config.json"
        model_path = f"{ckpt_path}/pytorch_model.bin"

        with open(config_path, "r") as handle:
            config_json = json.load(handle)
        hf_weights = torch.load(model_path)

        # TODO(joecummings): find better way to determine `encoder_only` and `linear_head`
        config = T5Conf(
            encoder_only="decoder.final_layer_norm.weight" not in hf_weights.keys(),
            linear_head="lm_head.weight" in hf_weights.keys(),
            embedding_dim=config_json["d_model"],
            num_attention_heads=config_json["num_heads"],
            num_encoder_layers=config_json["num_layers"],
            num_decoder_layers=config_json["num_decoder_layers"],
            ffn_dimension=config_json["d_ff"],
        )

        t5_model = T5Model(config, freeze_model)

        t5_model_state_dict = {
            "token_embeddings.weight": hf_weights["shared.weight"],
            "encoder.token_embeddings.weight": hf_weights["shared.weight"],
            "encoder.norm.weight": hf_weights["encoder.final_layer_norm.weight"],
            "encoder.layers.0.self_attn.relative_attention_bias.weight": hf_weights[
                "encoder.block.0.layer.0.SelfAttention.relative_attention_bias.weight"
            ],
        }
        # Convert encoder layers
        for i in range(config.num_encoder_layers):
            t5_model_state_dict[f"encoder.layers.{i}.linear1.weight"] = hf_weights[
                f"encoder.block.{i}.layer.1.DenseReluDense.wi.weight"
            ]
            t5_model_state_dict[f"encoder.layers.{i}.linear2.weight"] = hf_weights[
                f"encoder.block.{i}.layer.1.DenseReluDense.wo.weight"
            ]
            t5_model_state_dict[f"encoder.layers.{i}.norm1.weight"] = hf_weights[
                f"encoder.block.{i}.layer.0.layer_norm.weight"
            ]
            t5_model_state_dict[f"encoder.layers.{i}.norm2.weight"] = hf_weights[
                f"encoder.block.{i}.layer.1.layer_norm.weight"
            ]
            t5_model_state_dict[f"encoder.layers.{i}.self_attn.out_proj.weight"] = hf_weights[
                f"encoder.block.{i}.layer.0.SelfAttention.o.weight"
            ]
            t5_model_state_dict[f"encoder.layers.{i}.self_attn.q_proj_weight"] = hf_weights[
                f"encoder.block.{i}.layer.0.SelfAttention.q.weight"
            ]
            t5_model_state_dict[f"encoder.layers.{i}.self_attn.k_proj_weight"] = hf_weights[
                f"encoder.block.{i}.layer.0.SelfAttention.k.weight"
            ]
            t5_model_state_dict[f"encoder.layers.{i}.self_attn.v_proj_weight"] = hf_weights[
                f"encoder.block.{i}.layer.0.SelfAttention.v.weight"
            ]

        # Convert decoder layers if model is encoder-decoder
        if not config.encoder_only:
            t5_model_state_dict["decoder.norm.weight"] = hf_weights["decoder.final_layer_norm.weight"]
            t5_model_state_dict["decoder.layers.0.self_attn.relative_attention_bias.weight"] = hf_weights[
                "decoder.block.0.layer.0.SelfAttention.relative_attention_bias.weight"
            ]

            for i in range(config.num_decoder_layers):
                t5_model_state_dict[f"decoder.layers.{i}.linear1.weight"] = hf_weights[
                    f"decoder.block.{i}.layer.2.DenseReluDense.wi.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.linear2.weight"] = hf_weights[
                    f"decoder.block.{i}.layer.2.DenseReluDense.wo.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.norm1.weight"] = hf_weights[
                    f"decoder.block.{i}.layer.0.layer_norm.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.norm2.weight"] = hf_weights[
                    f"decoder.block.{i}.layer.2.layer_norm.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.norm3.weight"] = hf_weights[
                    f"decoder.block.{i}.layer.1.layer_norm.weight"
                ]

                t5_model_state_dict[f"decoder.layers.{i}.self_attn.out_proj.weight"] = hf_weights[
                    f"decoder.block.{i}.layer.0.SelfAttention.o.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.self_attn.q_proj_weight"] = hf_weights[
                    f"decoder.block.{i}.layer.0.SelfAttention.q.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.self_attn.k_proj_weight"] = hf_weights[
                    f"decoder.block.{i}.layer.0.SelfAttention.k.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.self_attn.v_proj_weight"] = hf_weights[
                    f"decoder.block.{i}.layer.0.SelfAttention.v.weight"
                ]

                t5_model_state_dict[f"decoder.layers.{i}.cross_attn.out_proj.weight"] = hf_weights[
                    f"decoder.block.{i}.layer.1.EncDecAttention.o.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.cross_attn.q_proj_weight"] = hf_weights[
                    f"decoder.block.{i}.layer.1.EncDecAttention.q.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.cross_attn.k_proj_weight"] = hf_weights[
                    f"decoder.block.{i}.layer.1.EncDecAttention.k.weight"
                ]
                t5_model_state_dict[f"decoder.layers.{i}.cross_attn.v_proj_weight"] = hf_weights[
                    f"decoder.block.{i}.layer.1.EncDecAttention.v.weight"
                ]

        # Convert language modeling head if there is one
        if config.linear_head:
            t5_model_state_dict["lm_head.weight"] = hf_weights["lm_head.weight"]

        # Load state dict into our model
        t5_model.load_state_dict(t5_model_state_dict, strict)

        return t5_model

    @property
    def config(self) -> T5Conf:
        return self._config


ENCODER_DOC = """
    T5_{}_ENCODER is an encoder-only model from a pre-trained T5 model with the {} configuration.
    It returns the normalized output from the final layer of the encoder.

    The T5 model was proposed in `Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer
    <http://jmlr.org/papers/v21/20-074.html>`. It introduces a unified framework that converts text-based
    language problems, such as translation, question-answering, and summarization, into a text-to-text format. The
    Colossal Clean Crawled Corpus (C4) dataset is used to pre-train the model on a masked language modeling task,
    and various datasets are used to fine-tune the model on each downstream task. The model's architecture is a modified version
    of the canonical Transformer architecture.

    Originally published by the authors of T5 under Apache License, Version 2.0
    and redistributed with the same license.
    [`License <https://github.com/google-research/text-to-text-transfer-transformer/blob/main/LICENSE>`__,
    `Source <https://github.com/google-research/text-to-text-transfer-transformer#released-model-checkpoints>`__]

    Please refer to :func:`torchtext.prototype.models.T5Bundle` for the usage.
"""

MODEL_DOC = """
    T5_{} is an encoder-decoder model from a pre-trained T5 model with the {} configuration.
    It returns the normalized output from the final layer of the decoder.

    The T5 model was proposed in `Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer
    <http://jmlr.org/papers/v21/20-074.html>`. It introduces a unified framework that converts text-based
    language problems, such as translation, question-answering, and summarization, into a text-to-text format. The
    Colossal Clean Crawled Corpus (C4) dataset is used to pre-train the model on a masked language modeling task,
    and various datasets are used to fine-tune the model on each downstream task. The model's architecture is a modified version
    of the canonical Transformer architecture.

    Originally published by the authors of T5 under Apache License, Version 2.0
    and redistributed with the same license.
    [`License <https://github.com/google-research/text-to-text-transfer-transformer/blob/main/LICENSE>`__,
    `Source <https://github.com/google-research/text-to-text-transfer-transformer#released-model-checkpoints>`__]

    Please refer to :func:`torchtext.prototype.models.T5Bundle` for the usage.
    """

GENERATION_DOC = """
    T5_{}_GENERATION is an encoder-decoder model from a pre-trained T5 model with the {} configuration.
    It returns the output of the final layer of the decoder after passing through a linear layer to project the hidden states to
    the model vocabulary. This output can then be used for language generation.

    The T5 model was proposed in `Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer
    <http://jmlr.org/papers/v21/20-074.html>`. It introduces a unified framework that converts text-based
    language problems, such as translation, question-answering, and summarization, into a text-to-text format. The
    Colossal Clean Crawled Corpus (C4) dataset is used to pre-train the model on a masked language modeling task,
    and various datasets are used to fine-tune the model on each downstream task. The model's architecture is a modified version
    of the canonical Transformer architecture.

    Originally published by the authors of T5 under Apache License, Version 2.0
    and redistributed with the same license.
    [`License <https://github.com/google-research/text-to-text-transfer-transformer/blob/main/LICENSE>`__,
    `Source <https://github.com/google-research/text-to-text-transfer-transformer#released-model-checkpoints>`__]

    Please refer to :func:`torchtext.prototype.models.T5Bundle` for the usage.
    """

T5_BASE_ENCODER = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.base.encoder.v2.pt"),
    _config=T5Conf(encoder_only=True),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_BASE_ENCODER.__doc__ = ENCODER_DOC.format("BASE", "base")

T5_BASE = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.base.v2.pt"),
    _config=T5Conf(encoder_only=False),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_BASE.__doc__ = MODEL_DOC.format("BASE", "base")

T5_BASE_GENERATION = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.base.generation.v2.pt"),
    _config=T5Conf(encoder_only=False, linear_head=True),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_BASE_GENERATION.__doc__ = GENERATION_DOC.format("BASE", "base")

T5_SMALL_ENCODER = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.small.encoder.v2.pt"),
    _config=T5Conf(
        encoder_only=True,
        embedding_dim=512,
        num_attention_heads=8,
        num_encoder_layers=6,
        num_decoder_layers=6,
        ffn_dimension=2048,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_SMALL_ENCODER.__doc__ = ENCODER_DOC.format("SMALL", "small")


T5_SMALL = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.small.v2.pt"),
    _config=T5Conf(
        encoder_only=False,
        embedding_dim=512,
        num_attention_heads=8,
        num_encoder_layers=6,
        num_decoder_layers=6,
        ffn_dimension=2048,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_SMALL.__doc__ = MODEL_DOC.format("SMALL", "small")

T5_SMALL_GENERATION = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.small.generation.v2.pt"),
    _config=T5Conf(
        encoder_only=False,
        linear_head=True,
        embedding_dim=512,
        num_attention_heads=8,
        num_encoder_layers=6,
        num_decoder_layers=6,
        ffn_dimension=2048,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_SMALL_GENERATION.__doc__ = GENERATION_DOC.format("SMALL", "small")

T5_LARGE_ENCODER = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.large.encoder.v2.pt"),
    _config=T5Conf(
        encoder_only=True,
        embedding_dim=1024,
        num_attention_heads=16,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=4096,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_LARGE_ENCODER.__doc__ = ENCODER_DOC.format("LARGE", "large")

T5_LARGE = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.large.v2.pt"),
    _config=T5Conf(
        encoder_only=False,
        embedding_dim=1024,
        num_attention_heads=16,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=4096,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_LARGE.__doc__ = MODEL_DOC.format("LARGE", "large")

T5_LARGE_GENERATION = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.large.generation.v2.pt"),
    _config=T5Conf(
        encoder_only=False,
        linear_head=True,
        embedding_dim=1024,
        num_attention_heads=16,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=4096,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_LARGE_GENERATION.__doc__ = GENERATION_DOC.format("LARGE", "large")

T5_3B_ENCODER = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.3b.encoder.v2.pt"),
    _config=T5Conf(
        encoder_only=True,
        embedding_dim=1024,
        qkv_dim=128,
        num_attention_heads=32,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=16384,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_3B_ENCODER.__doc__ = ENCODER_DOC.format("3B", "3B")

T5_3B = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.3b.v2.pt"),
    _config=T5Conf(
        encoder_only=False,
        embedding_dim=1024,
        qkv_dim=128,
        num_attention_heads=32,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=16384,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_3B.__doc__ = MODEL_DOC.format("3B", "3B")

T5_3B_GENERATION = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.3b.generation.v2.pt"),
    _config=T5Conf(
        encoder_only=False,
        linear_head=True,
        embedding_dim=1024,
        qkv_dim=128,
        num_attention_heads=32,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=16384,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_3B_GENERATION.__doc__ = GENERATION_DOC.format("3B", "3B")

T5_11B_ENCODER = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.11b.encoder.v2.pt"),
    _config=T5Conf(
        encoder_only=True,
        embedding_dim=1024,
        qkv_dim=128,
        num_attention_heads=128,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=65536,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_11B_ENCODER.__doc__ = ENCODER_DOC.format("11B", "11B")

T5_11B = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.11b.v2.pt"),
    _config=T5Conf(
        encoder_only=False,
        embedding_dim=1024,
        qkv_dim=128,
        num_attention_heads=128,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=65536,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_11B.__doc__ = MODEL_DOC.format("11B", "11B")

T5_11B_GENERATION = T5Bundle(
    _path=urljoin(_TEXT_BUCKET, "t5.11b.generation.v2.pt"),
    _config=T5Conf(
        encoder_only=False,
        linear_head=True,
        embedding_dim=1024,
        qkv_dim=128,
        num_attention_heads=128,
        num_encoder_layers=24,
        num_decoder_layers=24,
        ffn_dimension=65536,
    ),
    transform=lambda: T5Transform(
        urljoin(_TEXT_BUCKET, "t5_tokenizer_base.model"),
        max_seq_len=512,
        eos_idx=1,
        padding_idx=0,
    ),
)

T5_11B_GENERATION.__doc__ = GENERATION_DOC.format("11B", "11B")
