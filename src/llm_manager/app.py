"""LLM-Manager entrypoint. build_app(config_path) is testable without uvicorn;
main() wires logging + uvicorn.run using the configured host/port."""

from __future__ import annotations

import argparse
import pathlib

from llm_manager.bootstrap.container import AppContainer
from llm_manager.bootstrap.logging import setup_logging


def build_app(config_path: str | pathlib.Path):
    container = AppContainer(pathlib.Path(config_path))
    return container.app


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-Manager backend")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    args = parser.parse_args()

    setup_logging()
    import uvicorn

    app = build_app(args.config)
    program = app.state.container.config.program
    uvicorn.run(app, host=program.host, port=program.port)


if __name__ == "__main__":
    main()
