import logging

import uvicorn

from evallab import telemetry
from evallab.settings import WorkerSettings
from evallab.worker.app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = WorkerSettings()
    telemetry.configure_from_env("evallab-worker")
    try:
        uvicorn.run(create_app(settings), host=settings.health_host, port=settings.health_port)
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    main()
