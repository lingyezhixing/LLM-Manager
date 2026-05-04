from __future__ import annotations

import logging
from typing import Callable

from llm_manager.config.models import AppConfig
from llm_manager.container import Container

logger = logging.getLogger(__name__)


class SystemTray:
    def __init__(self, container: Container):
        self._container = container
        self._exit_callback: Callable | None = None
        self._is_headless = False

    @property
    def is_headless(self) -> bool:
        return self._is_headless

    def set_exit_callback(self, callback: Callable) -> None:
        self._exit_callback = callback

    def start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            logger.warning("pystray or Pillow not available, running in headless mode")
            self._is_headless = True
            return

        icon_image = Image.new("RGB", (64, 64), color="blue")
        draw = ImageDraw.Draw(icon_image)
        draw.rectangle([16, 16, 48, 48], fill="white")

        menu = pystray.Menu(
            pystray.MenuItem("Open WebUI", self._open_webui),
            pystray.MenuItem("Quit", self._quit),
        )

        self._icon = pystray.Icon("LLM-Manager", icon_image, "LLM-Manager", menu)
        self._icon.run()

    def _open_webui(self, icon, item):
        import webbrowser

        config = self._container.resolve(AppConfig)
        webbrowser.open(f"http://localhost:{config.program.port}")

    def _quit(self, icon, item):
        icon.stop()
        if self._exit_callback:
            self._exit_callback()

    def stop(self):
        if hasattr(self, "_icon"):
            self._icon.stop()
