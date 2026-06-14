"""Health-check probes, selected by model `mode` at cold start.

Each probe has the pinned signature (model_alias, port, start_time, timeout) -> (bool, str).
Bodies are moved verbatim from the former plugins/interfaces/*.py health_check methods.
"""
import openai
import time
from typing import Tuple
import logging

logger = logging.getLogger(__name__)


def probe_chat(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]:
    """聊天模型健康检查 - 先浅层检查，再深层检查"""
    if start_time is None:
        start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            break
        except Exception:
            time.sleep(2)
    else:
        return False, f"聊天探测器浅层检查超时: 服务在 {timeout} 秒内不可用"

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.chat.completions.create(
                model=model_alias,
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=1,
                stream=False,
                timeout=5.0,
            )
            return True, "聊天探测器健康检查成功"
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            pass
        except openai.APITimeoutError:
            pass
        except Exception as e:
            logger.warning(f"聊天探测器深层检查意外错误: {e}")
        time.sleep(1)

    return False, "聊天探测器深层检查超时"


def probe_base(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]:
    """基础文本补全模型健康检查 - 先浅层检查，再深层检查"""
    if start_time is None:
        start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            break
        except Exception:
            time.sleep(2)
    else:
        return False, f"基础探测器浅层检查超时: 服务在 {timeout} 秒内不可用"

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.completions.create(
                model=model_alias,
                prompt="hello",
                max_tokens=1,
                stream=False,
                timeout=5.0,
            )
            return True, "基础探测器健康检查成功"
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            pass
        except openai.APITimeoutError:
            pass
        except Exception as e:
            logger.warning(f"基础探测器深层检查意外错误: {e}")
        time.sleep(1)

    return False, "基础探测器深层检查超时"


def probe_embedding(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]:
    """嵌入向量模型健康检查 - 先浅层检查，再深层检查"""
    if start_time is None:
        start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            break
        except Exception:
            time.sleep(2)
    else:
        return False, f"嵌入探测器浅层检查超时: 服务在 {timeout} 秒内不可用"

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.embeddings.create(
                model=model_alias,
                input="hello",
                encoding_format="float",
                timeout=5.0,
            )
            return True, "嵌入探测器健康检查成功"
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            pass
        except openai.APITimeoutError:
            pass
        except Exception as e:
            logger.warning(f"嵌入探测器深层检查意外错误: {e}")
        time.sleep(1)

    return False, "嵌入探测器深层检查超时"


def probe_reranker(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]:
    """重排序模型健康检查 - 先浅层检查，再深层检查"""
    if start_time is None:
        start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            break
        except Exception:
            time.sleep(2)
    else:
        return False, f"重排序探测器浅层检查超时: 服务在 {timeout} 秒内不可用"

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            response = client._client.post(
                "rerank",
                json={
                    "model": model_alias,
                    "query": "hello",
                    "documents": ["hello world", "test document"],
                    "top_n": 1,
                },
                timeout=5.0,
            )
            response.raise_for_status()
            return True, "重排序探测器健康检查成功"
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            pass
        except openai.APITimeoutError:
            pass
        except Exception as e:
            logger.warning(f"重排序探测器深层检查意外错误: {e}")
        time.sleep(1)

    return False, "重排序探测器深层检查超时"


probe_registry = {
    "Chat": probe_chat,
    "Base": probe_base,
    "Embedding": probe_embedding,
    "Reranker": probe_reranker,
}
