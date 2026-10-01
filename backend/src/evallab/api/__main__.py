import logging

import uvicorn

from evallab import telemetry
from evallab.api.app import create_app
from evallab.settings import ApiSettings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = ApiSettings()
    telemetry.configure_from_env("evallab-api")
    try:
        uvicorn.run(create_app(), host=settings.host, port=settings.port)
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    main()
