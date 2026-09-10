"""系统托盘宿主:pystray 薄壳,持有 app.state 的协作者。

pystray 跑在自己的 daemon 线程上。同步动作(WOL UDP 发送、Claude settings
写入)直接在该线程执行;异步动作(重启自启模型、卸载全部)经
``run_coroutine_threadsafe`` 编组到 uvicorn 事件循环。``exit_app`` 在循环上
翻转 ``server.should_exit``,使 uvicorn 优雅关闭,lifespan 的 finally 块得以
执行(unload_all + 关闭客户端/DB)。

无头环境(无 pystray/Pillow,或 Linux 无 DISPLAY)降级为静默运行——
``is_tray_available`` 把关 ``start``。Icon/run() 循环不可单测;动作方法
不持 pystray 对象,可经 ``_run_coro_threadsafe`` 接缝单独演练。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import webbrowser
from pathlib import Path
from typing import Any

from llm_manager import config
from llm_manager.runtime import background
from llm_manager.tools import claude, wol

logger = logging.getLogger(__name__)

try:
    import pystray as _pystray  # type: ignore[import-not-found]
    from PIL import Image as _pil_image  # type: ignore[import-not-found]

    _PYSTRAY_AVAILABLE = True
except ImportError:
    _pystray = None
    _pil_image = None
    _PYSTRAY_AVAILABLE = False

# pystray/PIL 无类型存根:别名为 Any,属性访问不做检查;仅在 is_tray_available() 为真
# (即 import 成功)时才会解引用,故 None 路径不可达。
pystray: Any = _pystray
Image: Any = _pil_image


def _is_headless_display() -> bool:
    return os.name == "posix" and "DISPLAY" not in os.environ


def is_tray_available() -> bool:
    """仅当 pystray/Pillow 导入成功且存在显示环境时为真。"""
    if not _PYSTRAY_AVAILABLE:
        return False
    return not _is_headless_display()


class SystemTray:
    def __init__(
        self,
        *,
        lifecycle,
        get_cfg,
        monitor,
        loop: asyncio.AbstractEventLoop,
        server,
        settings_path,
        startup_timeout: float,
        auto_start_margin: float,
    ) -> None:
        self._lifecycle = lifecycle
        self._get_cfg = get_cfg
        self._monitor = monitor
        self._loop = loop
        self._server = server
        self._settings_path = Path(settings_path) if settings_path else None
        self._startup_timeout = startup_timeout
        self._auto_start_margin = auto_start_margin
        self._icon = None
        self._thread: threading.Thread | None = None

    # ---------- 生命周期 ----------
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
        except Exception:  # 托盘失败不应拖垮服务
            logger.exception("托盘运行失败,程序继续后台运行")

    def shutdown(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("托盘停止异常: %s", e)

    # ---------- 图标与菜单(需显示环境;不经单测) ----------
    def _load_image(self):
        """多帧 ICO:Windows 托盘按系统 DPI 选最接近帧渲染(16/24/32px)。
        assets/icon.ico 由 frontend/public/favicon.svg 一次性生成
        (16/24/32/48/64/128/256 七帧);读入 BytesIO 再开——Pillow 惰性打开
        会持有文件句柄,进程存活期间锁死 icon.ico,导致自更新的 git merge
        无法替换该文件(2026-08-24 实际踩坑)。多帧信息由 info['sizes'] 继承,
        pystray 序列化 ICO 时不丢帧;缺失时降级为空白图兜底。"""
        icon = Path(__file__).resolve().parents[0] / "assets" / "icon.ico"
        if not icon.exists():
            return Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        with icon.open("rb") as f:
            data = io.BytesIO(f.read())
        return Image.open(data)

    def _build_icon(self):
        cfg = self._get_cfg()
        items = [
            pystray.MenuItem("打开 WebUI", self.open_webui, default=True),
        ]
        if cfg.wol:
            items += [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("网络唤醒 NAS", self.send_wol),
            ]
        # 预设子菜单需目标 settings 路径(为空时写库会落到 cwd);未配置路径则隐藏该子菜单,
        # 托盘其余功能(WebUI/WOL/启停/退出)照常可用
        if cfg.claude_configs and cfg.program.claude_settings_path:
            submenu = pystray.Menu(*[self._preset_menuitem(n) for n in cfg.claude_configs])
            items.append(
                pystray.MenuItem("Claude 配置", submenu)
            )  # action=Menu 即子菜单(本版无 submenu kwarg)
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("重启自启模型", self.restart_auto_start),
            pystray.MenuItem("卸载全部模型", self.unload_all),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出程序", self.exit_app),
        ]
        return pystray.Icon("LLM-Manager", self._load_image(), "LLM-Manager", pystray.Menu(*items))

    def _preset_menuitem(self, name: str):
        # pystray _assert_action 要求 action 正好 1 或 2 参(数 inspect.signature 参数);
        # 故用闭包捕获 name,而非默认参数(action 留 (icon, item) 两参、checked 留 (item) 一参)。
        def action(icon, item):
            self.apply_claude(name)

        def checked(item):
            return self._current_preset() == name

        return pystray.MenuItem(name, action, checked=checked)

    def _current_preset(self) -> str:
        # 与 apply_claude 同款守卫:子菜单按活配置渲染、_settings_path 为构造时捕获,
        # 中途配置路径未重启时可能出现 None(Path(None) 会炸)。
        if not self._settings_path:
            return "(未知)"
        return claude.detect_current_preset(
            self._settings_path, dict(self._get_cfg().claude_configs)
        )

    # ---------- 动作(可单测;不持 pystray 对象) ----------
    def open_webui(self, icon=None, item=None) -> None:
        cfg = self._get_cfg()
        host = "localhost" if cfg.program.host == "0.0.0.0" else cfg.program.host
        webbrowser.open(f"http://{host}:{cfg.program.port}")

    def send_wol(self, icon=None, item=None) -> None:
        wol_cfg = self._get_cfg().wol
        if not wol_cfg:
            logger.warning("未配置网络唤醒(wake_on_lan)")
            return
        try:
            wol.send_wol(wol_cfg.mac_address, wol_cfg.broadcast_address)
            logger.info("网络唤醒包已发送到 %s", wol_cfg.broadcast_address)
        except Exception as e:  # noqa: BLE001
            logger.error("发送网络唤醒包失败: %s", e)

    def apply_claude(self, preset_name: str) -> None:
        if not self._settings_path:
            logger.warning("未配置 claude_settings_path,无法切换 Claude 配置")
            return
        preset = (self._get_cfg().claude_configs or {}).get(preset_name)
        if not preset:
            logger.error("未知 Claude 配置: %s", preset_name)
            return
        try:
            claude.apply_preset(self._settings_path, dict(preset))
            logger.info("Claude 配置已切换至 %s", preset_name)
        except Exception as e:  # noqa: BLE001
            logger.error("写入 Claude 配置失败: %s", e)

    def restart_auto_start(self, icon=None, item=None) -> None:
        self._run_coro_threadsafe(self._restart())

    def unload_all(self, icon=None, item=None) -> None:
        self._run_coro_threadsafe(self._lifecycle.unload_all())

    def exit_app(self, icon=None, item=None) -> None:
        # 优雅关闭:在循环上翻转 server.should_exit → uvicorn 关闭 →
        # lifespan 的 finally 执行(unload_all + 关闭客户端/DB)。
        self._loop.call_soon_threadsafe(setattr, self._server, "should_exit", True)
        if self._icon is not None:
            self._icon.stop()

    # ---------- 异步编组接缝 ----------
    def _run_coro_threadsafe(self, coro) -> None:
        if self._loop.is_closed():
            logger.warning("事件循环已关闭,操作取消")
            coro.close()  # 避免 "coroutine never awaited" 警告
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _restart(self) -> None:
        logger.info("重启自启模型...")
        await self._lifecycle.unload_all()
        cfg = self._get_cfg()
        auto_models = config.auto_start_models(cfg)
        stop_event = asyncio.Event()
        await background.auto_start(
            self._lifecycle,
            auto_models,
            cfg,
            self._monitor,
            timeout=self._startup_timeout + self._auto_start_margin,
            stop_event=stop_event,
        )
        logger.info("重启自启模型完成")
