"""Entry point: python -m brain.main"""
import uvicorn

from .api import create_app
from .config import Settings


def main() -> None:
    s = Settings()
    uvicorn.run(create_app(), host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
