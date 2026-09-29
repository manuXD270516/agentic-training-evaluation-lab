import uvicorn

from evallab.api.app import create_app
from evallab.settings import ApiSettings


def main() -> None:
    settings = ApiSettings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
