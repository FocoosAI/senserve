"""Load model catalog from TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    id: str
    display_name: str
    source: str
    capabilities: frozenset[str]
    preprocessor: str
    enabled: bool
    vllm: dict[str, Any] = field(default_factory=dict)
    default: bool = False

    def has_capability(self, name: str) -> bool:
        return name in self.capabilities


@dataclass
class ModelRegistry:
    models: dict[str, ModelSpec]

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise KeyError(f"Unknown model_id: {model_id}") from exc

    def default_model(self) -> ModelSpec | None:
        for spec in self.models.values():
            if spec.default and spec.enabled:
                return spec
        for spec in self.models.values():
            if spec.enabled:
                return spec
        return None

    def list_enabled(self) -> list[ModelSpec]:
        return [m for m in self.models.values() if m.enabled]


def _merge_toml(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, val in overlay.items():
        if key == "models" and isinstance(val, list):
            out.setdefault("models", []).extend(val)
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_toml(out[key], val)
        else:
            out[key] = val
    return out


_SENSERVE_ONLY_DEFAULTS = frozenset({"worker_port", "worker_base_port"})


def _default_vllm_opts(defaults: dict) -> dict[str, Any]:
    """Merge [defaults] and [defaults.vllm] into vLLM CLI options."""
    opts: dict[str, Any] = {
        k: v for k, v in defaults.items() if k not in _SENSERVE_ONLY_DEFAULTS and k != "vllm"
    }
    nested = defaults.get("vllm")
    if isinstance(nested, dict):
        opts.update(nested)
    return opts


def load_registry(path: Path | None = None) -> ModelRegistry:
    """Parse config/models.toml (+ optional models.local.toml)."""
    from senserve.settings import get_settings

    cfg_path = path or get_settings().models_path
    data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    local = cfg_path.parent / "models.local.toml"
    if local.is_file():
        data = _merge_toml(data, tomllib.loads(local.read_text(encoding="utf-8")))

    defaults = data.get("defaults", {})
    global_vllm = _default_vllm_opts(defaults)
    models: dict[str, ModelSpec] = {}
    default_ids: list[str] = []

    for entry in data.get("models", []):
        model_id = entry["id"]
        if model_id in models:
            raise ValueError(f"Duplicate model id: {model_id}")
        if entry.get("default"):
            default_ids.append(model_id)
        caps = entry.get("capabilities", ["text"])
        if isinstance(caps, str):
            caps = [caps]
        vllm = dict(global_vllm)
        vllm.update(entry.get("vllm") or {})
        models[model_id] = ModelSpec(
            id=model_id,
            display_name=entry.get("display_name", model_id),
            source=entry["source"],
            capabilities=frozenset(caps),
            preprocessor=entry.get("preprocessor", "none"),
            enabled=entry.get("enabled", True),
            vllm=vllm,
            default=bool(entry.get("default")),
        )

    if len(default_ids) > 1:
        raise ValueError(f"Multiple default models: {default_ids}")

    return ModelRegistry(models=models)
