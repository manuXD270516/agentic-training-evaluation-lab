import uvicorn

from evallab.settings import WorkerSettings
from evallab.worker.app import create_app


def main() -> None:
    settings = WorkerSettings()
    uvicorn.run(create_app(settings), host=settings.health_host, port=settings.health_port)


if __name__ == "__main__":
    main()
