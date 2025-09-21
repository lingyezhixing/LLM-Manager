import openai
import time
from typing import Dict, Any, Tuple, Optional, Set
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
import logging
from plugins.interfaces.Base_Class import InterfacePlugin

logger = logging.getLogger(__name__)

class BaseInterface(InterfacePlugin):
    """基础文本补全接口插件"""

    def __init__(self, model_manager=None):
        super().__init__("Base", model_manager)
        self.async_clients = {}


    def health_check(self, model_alias: str, port: int, start_time: float = None, timeout_seconds: int = 300) -> Tuple[bool, str]:
        """基础模型健康检查 - 先浅层检查，再深层检查"""
        if start_time is None:
            start_time = time.time()

        # 第一阶段：浅层检查 - 验证服务是否可用
        logger.debug(f"基础接口开始浅层检查: {model_alias}:{port}")
        while time.time() - start_time < timeout_seconds:
            try:
                client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
                client.models.list(timeout=3.0)
                logger.debug(f"基础接口浅层检查通过: {model_alias}:{port}")
                break
            except Exception as e:
                logger.debug(f"基础接口浅层检查失败: {e}")
                time.sleep(2)
        else:
            return False, f"基础接口浅层检查超时: 服务在 {timeout_seconds} 秒内不可用"

        # 第二阶段：深层检查 - 验证接口功能
        logger.debug(f"基础接口开始深层检查: {model_alias}:{port}")
        while time.time() - start_time < timeout_seconds:
            try:
                client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
                client.completions.create(
                    model=model_alias,
                    prompt="hello",
                    max_tokens=1,
                    stream=False,
                    timeout=5.0
                )
                logger.debug(f"基础接口深层检查通过: {model_alias}:{port}")
                return True, "基础接口健康检查成功"
            except openai.APIConnectionError as e:
                logger.debug(f"基础接口深层检查API连接错误: {e.__cause__}")
            except openai.APIStatusError as e:
                logger.debug(f"基础接口深层检查返回非成功状态码: {e.status_code} - {e.response}")
            except openai.APITimeoutError:
                logger.debug(f"基础接口深层检查请求超时")
            except Exception as e:
                logger.warning(f"基础接口深层检查期间出现意外错误: {e}")

            time.sleep(1)

        return False, "基础接口深层检查超时"

    def get_supported_endpoints(self) -> Set[str]:
        """获取基础接口支持的API端点"""
        return {"v1/completions"}

    def validate_request(self, path: str, model_alias: str) -> Tuple[bool, str]:
        """验证请求路径是否适合基础接口"""
        is_chat_endpoint = "v1/chat/completions" in path
        is_completion_endpoint = "v1/completions" in path

        if is_chat_endpoint:
            return False, f"模型 '{model_alias}' 是 'Base' 模式, 不支持聊天补全接口"

        return True, ""

    