"""Run the local public API with the website's browser-safe configuration."""

from __future__ import annotations

import uvicorn

from api.local_config import load_local_public_config


def main() -> None:
    load_local_public_config()
    uvicorn.run("api.public_app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
