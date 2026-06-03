from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from senserve.registry import ModelRegistry, ModelSpec


def _registry_with_default() -> ModelRegistry:
    spec = ModelSpec(
        id="m1",
        display_name="M1",
        source="hf/m1",
        capabilities=frozenset(["text"]),
        enabled=True,
        default=True,
    )
    return ModelRegistry(models={"m1": spec})


def _registry_no_default() -> ModelRegistry:
    spec = ModelSpec(
        id="m1",
        display_name="M1",
        source="hf/m1",
        capabilities=frozenset(["text"]),
        enabled=True,
        default=False,
    )
    return ModelRegistry(models={"m1": spec})


@patch("senserve.cli.uvicorn.run")
@patch("senserve.cli.create_app")
@patch("senserve.cli.EngineSupervisor")
def test_startup_loads_default_model(mock_sup_cls, _mock_app, _mock_uvicorn):
    sup = MagicMock()
    sup.registry = _registry_with_default()
    mock_sup_cls.return_value = sup

    with patch("sys.argv", ["senserve"]):
        from senserve.cli import main

        main()

    sup.load_blocking.assert_called_once_with("m1")


@patch("senserve.cli.uvicorn.run")
@patch("senserve.cli.create_app")
@patch("senserve.cli.EngineSupervisor")
def test_startup_idle_without_default(mock_sup_cls, _mock_app, _mock_uvicorn):
    sup = MagicMock()
    sup.registry = _registry_no_default()
    mock_sup_cls.return_value = sup

    with patch("sys.argv", ["senserve"]):
        from senserve.cli import main

        main()

    sup.load_blocking.assert_not_called()


@patch("senserve.cli.uvicorn.run")
@patch("senserve.cli.create_app")
@patch("senserve.cli.EngineSupervisor")
def test_startup_no_load_flag(mock_sup_cls, _mock_app, _mock_uvicorn):
    sup = MagicMock()
    sup.registry = _registry_with_default()
    mock_sup_cls.return_value = sup

    with patch("sys.argv", ["senserve", "--no-load"]):
        from senserve.cli import main

        main()

    sup.load_blocking.assert_not_called()
