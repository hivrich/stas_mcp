import argparse
import importlib
from typing import Any

from .server import serve


def run(app: Any, host: str = "127.0.0.1", port: int = 8000) -> None:
    serve(app, host, port)


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal uvicorn stub")
    parser.add_argument("app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    module_name, app_name = args.app.split(":", 1)
    module = importlib.import_module(module_name)
    app = getattr(module, app_name)
    serve(app, args.host, args.port)


if __name__ == "__main__":
    main()
