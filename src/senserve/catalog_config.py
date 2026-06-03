"""Load/save model catalog YAML and Pydantic validation for admin API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from senserve.registry import (
    ModelRegistry,
    _local_overlay_path,
    _load_yaml_file,
    _merge_config,
    document_to_registry,
)
from senserve.settings import get_settings

_MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_KNOWN_CAPABILITIES = frozenset({"text", "vision", "video", "image"})


class ModelEntry(BaseModel):
    id: str
    display_name: str | None = None
    source: str
    capabilities: list[str] = Field(default_factory=lambda: ["text"])
    enabled: bool = True
    default: bool = False
    vllm: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not _MODEL_ID_RE.match(v):
            raise ValueError(f"Invalid model id: {v!r}")
        return v

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("capabilities must not be empty")
        unknown = set(v) - _KNOWN_CAPABILITIES
        if unknown:
            raise ValueError(f"Unknown capabilities: {sorted(unknown)}")
        return v

    def to_catalog_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "capabilities": list(self.capabilities),
            "enabled": self.enabled,
            "default": self.default,
        }
        if self.display_name and self.display_name != self.id:
            out["display_name"] = self.display_name
        if self.vllm:
            out["vllm"] = dict(self.vllm)
        return out


class ConfigDocument(BaseModel):
    defaults: dict[str, Any] = Field(default_factory=dict)
    models: list[ModelEntry]

    @model_validator(mode="after")
    def check_models(self) -> ConfigDocument:
        ids = [m.id for m in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate model ids in catalog")
        defaults = [m.id for m in self.models if m.default and m.enabled]
        if len(defaults) > 1:
            raise ValueError(f"Multiple default models: {defaults}")
        return self

    def to_catalog_dict(self) -> dict[str, Any]:
        return {
            "defaults": dict(self.defaults),
            "models": [m.to_catalog_dict() for m in self.models],
        }


class ConfigConflictError(Exception):
    """PUT blocked because the active model would be affected."""


def models_path(path: Path | None = None) -> Path:
    return path or get_settings().models_path


def load_config_document(path: Path | None = None) -> dict[str, Any]:
    """Load base models.yaml only (no local overlay)."""
    cfg_path = models_path(path)
    return _load_yaml_file(cfg_path)


def load_merged_document(path: Path | None = None) -> dict[str, Any]:
    """Load base catalog merged with models.local.yaml when present."""
    cfg_path = models_path(path)
    data = _load_yaml_file(cfg_path)
    local = _local_overlay_path(cfg_path)
    if local.is_file() and local != cfg_path:
        data = _merge_config(data, _load_yaml_file(local))
    return data


def load_local_overlay(path: Path | None = None) -> dict[str, Any] | None:
    cfg_path = models_path(path)
    local = _local_overlay_path(cfg_path)
    if local.is_file() and local != cfg_path:
        return _load_yaml_file(local)
    return None


def save_config_document(doc: ConfigDocument, path: Path | None = None) -> Path:
    """Validate and atomically write the base catalog file."""
    cfg_path = models_path(path)
    payload = doc.to_catalog_dict()
    text = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    tmp = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(cfg_path)
    return cfg_path


def registry_from_document(data: dict[str, Any]) -> ModelRegistry:
    return document_to_registry(data)


def preview_registry(doc: ConfigDocument, path: Path | None = None) -> ModelRegistry:
    """Effective registry after applying base doc + existing local overlay."""
    base = doc.to_catalog_dict()
    local = load_local_overlay(path)
    if local:
        base = _merge_config(base, local)
    return registry_from_document(base)


def config_affects_active_model(
    old_reg: ModelRegistry,
    new_reg: ModelRegistry,
    active_id: str | None,
) -> bool:
    if not active_id:
        return False
    if active_id not in new_reg.models:
        return True
    try:
        old = old_reg.get(active_id)
    except KeyError:
        return True
    new = new_reg.get(active_id)
    if not new.enabled:
        return True
    if old.source != new.source:
        return True
    if old.vllm != new.vllm:
        return True
    return False


def document_from_registry(reg: ModelRegistry, defaults: dict[str, Any]) -> ConfigDocument:
    """Build editable document from registry (per-model vllm = overrides only)."""
    from senserve.registry import _default_vllm_opts

    merged_global = _default_vllm_opts(defaults)
    models: list[ModelEntry] = []
    for spec in sorted(reg.models.values(), key=lambda s: s.id):
        per_model_vllm = {k: v for k, v in spec.vllm.items() if merged_global.get(k) != v}
        models.append(
            ModelEntry(
                id=spec.id,
                display_name=spec.display_name,
                source=spec.source,
                capabilities=sorted(spec.capabilities),
                enabled=spec.enabled,
                default=spec.default,
                vllm=per_model_vllm,
            )
        )
    return ConfigDocument(defaults=dict(defaults), models=models)
