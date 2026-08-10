from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.config import get_settings  # noqa: E402
from backend.app.launcher import available_loopback_port  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from backend.app.novelai.mock import MockNovelAIClient  # noqa: E402


def main() -> None:
    settings = get_settings()
    port = settings.bind_port or available_loopback_port()
    app = create_app(settings)
    provider = MockNovelAIClient()
    app.state.novelai.provider_factory = lambda _configuration, _secret_reader: provider
    app.state.generation_executor.provider_factory = (
        lambda _configuration, _secret_reader: provider
    )
    session = app.state.local_session
    url = f"http://{settings.bind_host}:{port}/#session={session.token}&csrf={session.csrf_token}"
    threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
