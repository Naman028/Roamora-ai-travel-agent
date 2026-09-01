"""Load a base model and optionally attach a PEFT/LoRA adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def has_complete_json_object(text: str) -> bool:
    """Return True once the first root JSON object is structurally complete."""
    started = False
    depth = 0
    in_string = False
    escaped = False

    for char in text:
        if not started:
            if char != "{":
                continue
            started = True
            depth = 1
            continue

        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return True

    return False


def load_env_file(path: str | os.PathLike[str] | None = None) -> None:
    """Load environment variables from .env files when python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    if path:
        load_dotenv(path)
        return

    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir.parents[1] / ".env",
        script_dir.parents[1] / ".env.example",
    ]
    for candidate in candidates:
        if candidate.exists():
            load_dotenv(candidate, override=False)


@dataclass(frozen=True)
class AdapterConfig:
    base_model_id: str
    adapter_repo_id: str
    adapter_repo_type: str
    adapter_subfolder: str
    local_adapter_path: str
    model_temperature: float
    model_max_input_tokens: int
    model_max_new_tokens: int
    hf_token: str | None

    @classmethod
    def from_env(cls) -> "AdapterConfig":
        load_env_file()
        return cls(
            base_model_id=os.getenv("BASE_MODEL_ID", "unsloth/Llama-3.2-3B-Instruct"),
            adapter_repo_id=os.getenv("ADAPTER_REPO_ID", "Naman-1718/wanderly-training-backup"),
            adapter_repo_type=os.getenv("ADAPTER_REPO_TYPE", "dataset").strip() or "model",
            adapter_subfolder=os.getenv("ADAPTER_SUBFOLDER", "wanderly-3b-lora/checkpoint-1952").strip("/"),
            local_adapter_path=os.getenv("LOCAL_ADAPTER_PATH", "").strip(),
            model_temperature=float(os.getenv("MODEL_TEMPERATURE", "0.0")),
            model_max_input_tokens=int(os.getenv("MODEL_MAX_INPUT_TOKENS", "2000")),
            model_max_new_tokens=int(os.getenv("MODEL_MAX_NEW_TOKENS", "1200")),
            hf_token=os.getenv("HF_TOKEN") or os.getenv("HF_API_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN"),
        )


def _adapter_kwargs(config: AdapterConfig) -> dict[str, str]:
    kwargs: dict[str, str] = {}
    if not config.local_adapter_path and config.adapter_subfolder:
        kwargs["subfolder"] = config.adapter_subfolder
    return kwargs


def _hub_download_kwargs(config: AdapterConfig) -> dict[str, str | None]:
    kwargs: dict[str, str | None] = {
        "repo_id": config.adapter_repo_id,
        "token": config.hf_token,
        **_adapter_kwargs(config),
    }
    if config.adapter_repo_type and config.adapter_repo_type != "model":
        kwargs["repo_type"] = config.adapter_repo_type
    return kwargs


def _peft_adapter_kwargs(config: AdapterConfig) -> dict[str, str]:
    if config.local_adapter_path or config.adapter_repo_type != "model":
        return {}
    return _adapter_kwargs(config)


def _load_peft_adapter(model, config: AdapterConfig):
    from peft import PeftModel

    source = adapter_source(config)
    if isinstance(source, Path):
        print(f"PEFT adapter load : local folder {source}")
        return PeftModel.from_pretrained(model, source)
    print(f"PEFT adapter load : Hub model repo {source}")
    return PeftModel.from_pretrained(
        model,
        source,
        token=config.hf_token,
        **_peft_adapter_kwargs(config),
    )


def adapter_source(config: AdapterConfig) -> str | Path:
    if config.local_adapter_path:
        return Path(config.local_adapter_path)
    if config.adapter_repo_type == "model":
        return config.adapter_repo_id
    return _download_dataset_adapter(config)


def resolved_adapter_source(config: AdapterConfig | None = None) -> str | Path:
    """Return the source PEFT will use after dataset downloads are resolved locally."""
    config = config or AdapterConfig.from_env()
    validate_adapter_files(config)
    return adapter_source(config)


def validate_adapter_files(config: AdapterConfig) -> None:
    """Ensure the adapter source contains the PEFT files we need."""
    if config.local_adapter_path:
        _validate_local_adapter_dir(Path(config.local_adapter_path))
        return

    if config.adapter_repo_type != "model":
        _validate_local_adapter_dir(_download_dataset_adapter(config))
        return

    from huggingface_hub import hf_hub_download

    common_kwargs = _hub_download_kwargs(config)
    hf_hub_download(filename="adapter_config.json", **common_kwargs)
    hf_hub_download(filename="adapter_model.safetensors", **common_kwargs)


def _validate_local_adapter_dir(adapter_dir: Path) -> None:
    missing = [
        name
        for name in ("adapter_config.json", "adapter_model.safetensors")
        if not (adapter_dir / name).exists()
    ]
    if missing:
        raise FileNotFoundError(f"Missing adapter file(s) in {adapter_dir}: {', '.join(missing)}")


def _download_dataset_adapter(config: AdapterConfig) -> Path:
    from huggingface_hub import snapshot_download

    script_dir = Path(__file__).resolve().parent
    local_dir = script_dir.parent / "checkpoints" / config.adapter_repo_id.replace("/", "__")
    adapter_dir = local_dir / config.adapter_subfolder
    if all(
        (adapter_dir / name).exists()
        for name in ("adapter_config.json", "adapter_model.safetensors")
    ):
        return adapter_dir

    allow_patterns = [
        f"{config.adapter_subfolder}/adapter_config.json",
        f"{config.adapter_subfolder}/adapter_model.safetensors",
    ]
    snapshot_download(
        repo_id=config.adapter_repo_id,
        repo_type=config.adapter_repo_type,
        token=config.hf_token,
        allow_patterns=allow_patterns,
        local_dir=local_dir,
    )
    return adapter_dir


def load_base_model_and_adapter(config: AdapterConfig | None = None):
    """Return tokenizer and model with the LoRA adapter attached."""
    config = config or AdapterConfig.from_env()
    validate_adapter_files(config)

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        config.base_model_id,
        token=config.hf_token,
        trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
    model_kwargs = {
        "token": config.hf_token,
        "dtype": dtype if torch.cuda.is_available() else torch.float32,
        "trust_remote_code": True,
    }
    if torch.cuda.is_available():
        model_kwargs["device_map"] = "auto"

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model_id,
        **model_kwargs,
    )
    model = _load_peft_adapter(model, config)
    model.eval()
    return tokenizer, model


def load_base_model_only(config: AdapterConfig | None = None):
    """Return tokenizer and the base model without attaching the adapter."""
    config = config or AdapterConfig.from_env()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        config.base_model_id,
        token=config.hf_token,
        trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
    model_kwargs = {
        "token": config.hf_token,
        "dtype": dtype if torch.cuda.is_available() else torch.float32,
        "trust_remote_code": True,
    }
    if torch.cuda.is_available():
        model_kwargs["device_map"] = "auto"

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model_id,
        **model_kwargs,
    )
    model.eval()
    return tokenizer, model


def generate_text(tokenizer, model, prompt: str, config: AdapterConfig) -> str:
    """Generate text from a single user prompt."""
    import torch
    from transformers import StoppingCriteria, StoppingCriteriaList

    messages = [{"role": "user", "content": prompt}]
    if getattr(tokenizer, "chat_template", None):
        input_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    else:
        input_text = prompt

    inputs = tokenizer(
        input_text,
        return_tensors="pt",
        truncation=True,
        max_length=config.model_max_input_tokens,
    ).to(model.device)
    do_sample = config.model_temperature > 0

    class _CompleteJsonStoppingCriteria(StoppingCriteria):
        def __init__(self, prompt_length: int):
            self.prompt_length = prompt_length

        def __call__(self, input_ids, scores, **kwargs):
            generated = tokenizer.decode(
                input_ids[0][self.prompt_length :],
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
            return has_complete_json_object(generated)

    stopping_criteria = StoppingCriteriaList(
        [_CompleteJsonStoppingCriteria(inputs["input_ids"].shape[-1])]
    )
    with torch.inference_mode():
        output_ids = model.generate(
            **inputs,
            max_length=None,
            max_new_tokens=config.model_max_new_tokens,
            use_cache=True,
            do_sample=do_sample,
            temperature=config.model_temperature if do_sample else None,
            top_p=0.9 if do_sample else None,
            pad_token_id=tokenizer.eos_token_id,
            stopping_criteria=stopping_criteria,
        )

    generated_ids = output_ids[0][inputs["input_ids"].shape[-1] :]
    return tokenizer.decode(
        generated_ids,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    ).strip()


def print_config(config: AdapterConfig) -> None:
    subfolder = config.adapter_subfolder or "<repo root>"
    print(f"Base model      : {config.base_model_id}")
    if config.local_adapter_path:
        print(f"Local adapter   : {config.local_adapter_path}")
    else:
        print(f"Adapter repo    : {config.adapter_repo_id}")
        print(f"Adapter type    : {config.adapter_repo_type}")
        print(f"Adapter folder  : {subfolder}")
    print(f"Temperature     : {config.model_temperature}")
    print(f"Max input tokens: {config.model_max_input_tokens}")
    print(f"Max new tokens  : {config.model_max_new_tokens}")
    print(f"HF token present: {bool(config.hf_token)}")
