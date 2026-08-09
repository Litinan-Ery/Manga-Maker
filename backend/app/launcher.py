from __future__ import annotations

import socket
import threading
import webbrowser

import uvicorn

from .config import get_settings
from .main import create_app


def available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def main() -> None:
    settings = get_settings()
    port = settings.bind_port or available_loopback_port()
    app = create_app(settings)
    session = app.state.local_session
    url = f"http://{settings.bind_host}:{port}/#session={session.token}&csrf={session.csrf_token}"
    threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=settings.bind_host, port=port, log_level="info")


if __name__ == "__main__":
    main()
