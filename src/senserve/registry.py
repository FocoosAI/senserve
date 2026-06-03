"""Load model catalog from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ModelSpec:
    id: str
    display_name: str
    source: str
    capabilities: frozenset[str]
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
        """First enabled model explicitly marked default: true in catalog."""
        for spec in self.models.values():
            if spec.default and spec.enabled:
                return spec
        return None

    def list_enabled(self) -> list[ModelSpec]:
        return [m for m in self.models.values() if m.enabled]


def _merge_config(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, val in overlay.items():
        if key == "models" and isinstance(val, list):
            out.setdefault("models", []).extend(val)
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_config(out[key], val)
        else:
            out[key] = val
    return out


_SENSERVE_ONLY_DEFAULTS = frozenset({"worker_port", "worker_base_port"})


def _default_vllm_opts(defaults: dict) -> dict[str, Any]:
    """Merge defaults and defaults.vllm into vLLM CLI options."""
    opts: dict[str, Any] = {
        k: v for k, v in defaults.items() if k not in _SENSERVE_ONLY_DEFAULTS and k != "vllm"
    }
    nested = defaults.get("vllm")
    if isinstance(nested, dict):
        opts.update(nested)
    return opts


def _load_yaml_file(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        raise ValueError(
            f"Model catalog must be YAML (.yaml/.yml), not TOML: {path}. "
            "See config/models.yaml."
        )
    if suffix not in (".yaml", ".yml"):
        raise ValueError(f"Model catalog must be a .yaml or .yml file: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Model catalog root must be a mapping: {path}")
    return raw


def _local_overlay_path(cfg_path: Path) -> Path:
    stem = cfg_path.stem
    if stem.endswith(".local"):
        return cfg_path
    return cfg_path.parent / f"{stem}.local.yaml"


def document_to_registry(data: dict) -> ModelRegistry:
    """Build ModelRegistry from a merged or base config document."""
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
            enabled=entry.get("enabled", True),
            vllm=vllm,
            default=bool(entry.get("default")),
        )

    if len(default_ids) > 1:
        raise ValueError(f"Multiple default models: {default_ids}")

    return ModelRegistry(models=models)


def load_registry(path: Path | None = None) -> ModelRegistry:
    """Parse config/models.yaml (+ optional models.local.yaml)."""
    from senserve.catalog_config import load_merged_document

    return document_to_registry(load_merged_document(path))
