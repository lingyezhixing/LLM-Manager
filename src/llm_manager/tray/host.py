"""System tray host: pystray thin shell holding app.state collaborators.

pystray runs on its own daemon thread. Sync actions (WOL UDP send, Claude
settings write) run directly on that thread; async actions (restart auto-start
models, unload all) marshal to the uvicorn loop via ``run_coroutine_threadsafe``.
``exit_app`` flips ``server.should_exit`` on the loop so uvicorn shuts down
gracefully and the lifespan finally-block runs (unload_all + close clients/db).

Headless environments (no pystray/Pillow, or Linux without DISPLAY) degrade to
silent operation — ``is_tray_available`` gates ``start``. The Icon/run() loop is
not unit-testable; action methods are kept free of pystray objects so they can
be exercised in isolation via the ``_run_coro_threadsafe`` seam.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import webbrowser
from pathlib import Path

from llm_manager.runtime import background
from llm_manager.tray import claude, wol

logger = logging.getLogger(__name__)

try:
    import pystray  # type: ignore[import-not-found]
    from PIL import Image  # type: ignore[import-not-found]
    _PYSTRAY_AVAILABLE = True
except ImportError:
    pystray = None
    Image = None
    _PYSTRAY_AVAILABLE = False


def _is_headless_display() -> bool:
    return os.name == "posix" and "DISPLAY" not in os.environ


def is_tray_available() -> bool:
    """True only when pystray/Pillow import OK and a display is present."""
    if not _PYSTRAY_AVAILABLE:
        return False
    return not _is_headless_display()


class SystemTray:
    def __init__(
        self,
        *,
        lifecycle,
        cfg,
        monitor,
        loop: asyncio.AbstractEventLoop,
        server,
        settings_path,
        startup_timeout: float,
        auto_start_margin: float,
        icon_path=None,
    ) -> None:
        self._lifecycle = lifecycle
        self._cfg = cfg
        self._monitor = monitor
        self._loop = loop
        self._server = server
        self._settings_path = Path(settings_path)
        self._startup_timeout = startup_timeout
        self._auto_start_margin = auto_start_margin
        self._icon_path = Path(icon_path) if icon_path else None
        self._icon = None
        self._thread: threading.Thread | None = None

    # ---------- availability ----------
    @staticmethod
    def is_tray_available() -> bool:
        return is_tray_available()

    # ---------- lifecycle ----------
    def start(self) -> None:
        if not is_tray_available():
            logger.info("无头模式:系统托盘未启动(后台静默运行)")
            return
        self._thread = threading.Thread(target=self._run_icon, daemon=True, name="TrayUI")
        self._thread.start()

    def _run_icon(self) -> None:
        try:
            self._icon = self._build_icon()
            self._icon.run()
        except Exception as e:  # 托盘失败不应拖垮服务
            logger.error("托盘运行失败,程序继续后台运行: %s", e)

    def shutdown(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as e:
                logger.warning("托盘停止异常: %s", e)

    # ---------- icon + menu (need display; not unit-tested) ----------
    def _load_image(self):
        candidates = [self._icon_path] if self._icon_path else [
            Path(__file__).resolve().parents[3] / "icons" / "icon.ico",
        ]
        for p in candidates:
            if p and p.exists():
                return Image.open(p)
        return Image.new("RGB", (64, 64), "black")

    def _build_icon(self):
        items = [
            pystray.MenuItem("🌐 打开 WebUI", self.open_webui, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔔 网络唤醒飞牛", self.send_wol),
        ]
        if self._cfg.claude_configs:
            submenu = pystray.Menu(*[self._preset_menuitem(n) for n in self._cfg.claude_configs])
            items.append(pystray.MenuItem("🔄 Claude 配置", None, submenu=submenu))
        items += [
            pystray.MenuItem("▶ 重启自启模型", self.restart_auto_start),
            pystray.MenuItem("⏹ 卸载全部模型", self.unload_all),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ 退出程序", self.exit_app),
        ]
        return pystray.Icon("LLM-Manager", self._load_image(), "LLM-Manager", pystray.Menu(*items))

    def _preset_menuitem(self, name: str):
        return pystray.MenuItem(
            name,
            lambda icon, item, n=name: self.apply_claude(n),
            checked=lambda item, n=name: self._current_preset() == n,
        )

    def _current_preset(self) -> str:
        return claude.detect_current_preset(self._settings_path, dict(self._cfg.claude_configs))

    # ---------- actions (unit-testable; no pystray objects) ----------
    def open_webui(self, icon=None, item=None) -> None:
        host = "localhost" if self._cfg.program.host == "0.0.0.0" else self._cfg.program.host
        webbrowser.open(f"http://{host}:{self._cfg.program.port}")

    def send_wol(self, icon=None, item=None) -> None:
        wol_cfg = self._cfg.wol
        if not wol_cfg:
            logger.warning("未配置网络唤醒(wake_on_lan)")
            return
        try:
            wol.send_wol(wol_cfg.mac_address, wol_cfg.broadcast_address)
            logger.info("网络唤醒包已发送到 %s", wol_cfg.broadcast_address)
        except Exception as e:
            logger.error("发送网络唤醒包失败: %s", e)

    def apply_claude(self, preset_name: str) -> None:
        preset = (self._cfg.claude_configs or {}).get(preset_name)
        if not preset:
            logger.error("未知 Claude 配置: %s", preset_name)
            return
        try:
            claude.apply_preset(self._settings_path, dict(preset))
            logger.info("Claude 配置已切换至 %s", preset_name)
        except Exception as e:
            logger.error("写入 Claude 配置失败: %s", e)

    def restart_auto_start(self, icon=None, item=None) -> None:
        self._run_coro_threadsafe(self._restart())

    def unload_all(self, icon=None, item=None) -> None:
        self._run_coro_threadsafe(self._lifecycle.unload_all())

    def exit_app(self, icon=None, item=None) -> None:
        # graceful: flip server.should_exit on the loop → uvicorn shuts down →
        # lifespan finally runs (unload_all + close clients/db).
        self._loop.call_soon_threadsafe(setattr, self._server, "should_exit", True)
        if self._icon is not None:
            self._icon.stop()

    # ---------- async marshal seam ----------
    def _run_coro_threadsafe(self, coro) -> None:
        if self._loop.is_closed():
            logger.warning("事件循环已关闭,操作取消")
            coro.close()        # 避免 "coroutine never awaited" 警告
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _restart(self) -> None:
        logger.info("重启自启模型...")
        await self._lifecycle.unload_all()
        auto_models = [n for n, m in self._cfg.models.items() if m.auto_start]
        stop_event = asyncio.Event()
        await background.auto_start(
            self._lifecycle, auto_models, self._cfg, self._monitor,
            timeout=self._startup_timeout + self._auto_start_margin,
            stop_event=stop_event,
        )
        logger.info("重启自启模型完成")
