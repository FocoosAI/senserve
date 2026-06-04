"""CLI entrypoint for the Senserve gateway."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from senserve import vllm_flags
from senserve.engine import EngineSupervisor
from senserve.gateway.app import create_app
from senserve.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Senserve gateway")
    parser.add_argument(
        "--load",
        metavar="MODEL_ID",
        help="Load this model on startup (default from config)",
    )
    parser.add_argument(
        "--no-load",
        action="store_true",
        help="Start gateway without loading a model",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    settings = get_settings()

    logging.info("Preloading vLLM serve --help flags…")
    try:
        vllm_flags.preload_at_startup()
    except Exception:
        logging.warning(
            "vLLM flags preload failed; config autocomplete may be unavailable until refresh",
            exc_info=True,
        )

    supervisor = EngineSupervisor()

    if not args.no_load:
        model_id = args.load
        if not model_id:
            default = supervisor.registry.default_model()
            if default is None:
                logging.info("No default model in catalog; starting idle (use Load or --load)")
            else:
                model_id = default.id
        if model_id:
            try:
                supervisor.load(model_id)
            except Exception:
                logging.exception("Startup model load failed")
                sys.exit(1)

    app = create_app(supervisor)
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port, log_level="info")


if __name__ == "__main__":
    main()
