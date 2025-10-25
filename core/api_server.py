from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Dict, List, Optional, Any
from types import SimpleNamespace
import json
import time
import asyncio
import queue
import pandas as pd
import numpy as np
from utils.logger import get_logger
from core.config_manager import ConfigManager
from core.model_controller import ModelController
from core.data_manager import Monitor, ModelRequest, ModelBilling, TierPricing
from core.api_router import APIRouter, TokenTracker

logger = get_logger(__name__)


class APIServer:
    """API服务器 - 负责FastAPI应用管理和路由配置"""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.model_controller = ModelController(self.config_manager)
        self.monitor = Monitor()
        self.token_tracker = TokenTracker(self.monitor, self.config_manager)
        self.api_router = APIRouter(self.config_manager, self.model_controller)
        self.app = FastAPI(title="LLM-Manager API", version="1.0.0")
        self._setup_routes()
        self.model_controller.start_auto_start_models()
        logger.info("API服务器初始化完成")
        logger.debug("[API_SERVER] token跟踪功能已激活")

    def _find_matching_tier(self, input_tokens: int, output_tokens: int, tiers: List[TierPricing]) -> Optional[TierPricing]:
        """
        根据输入和输出token数量匹配唯一的阶梯

        Args:
            input_tokens: 输入token数量
            output_tokens: 输出token数量
            tiers: 所有阶梯配置

        Returns:
            匹配的阶梯，如果没有匹配则返回None
        """
        for tier in tiers:
            # 检查输入token范围（注意：min是不包含，max是包含，-1表示无上限）
            input_match = (input_tokens > tier.min_input_tokens and
                          (tier.max_input_tokens == -1 or input_tokens <= tier.max_input_tokens))

            # 检查输出token范围（注意：min是不包含，max是包含，-1表示无上限）
            output_match = (output_tokens > tier.min_output_tokens and
                           (tier.max_output_tokens == -1 or output_tokens <= tier.max_output_tokens))

            if input_match and output_match:
                return tier

        return None

    def _calculate_request_cost(self, req: ModelRequest, billing: Optional[ModelBilling]) -> float:
        """
        【修正版】计算单个请求的成本，确保与向量化版本逻辑完全一致

        注意：此函数用于单次请求场景，批量计算请使用 _calculate_cost_vectorized
        """
        if not billing or not billing.use_tier_pricing or not billing.tier_pricing:
            return 0.0

        # 1. 根据输入和输出token数量匹配唯一阶梯
        matching_tier = self._find_matching_tier(req.input_tokens, req.output_tokens, billing.tier_pricing)

        if not matching_tier:
            logger.warning(f"未找到匹配的计费阶梯: input={req.input_tokens}, output={req.output_tokens} for model. 请求成本计为0。")
            return 0.0

        total_cost = 0.0

        # 2. 根据阶梯配置计算成本
        if matching_tier.support_cache:
            # **支持缓存的计费逻辑**
            # 成本 = 缓存读取成本 + 缓外输入成本 + 输出内容成本 + 缓存写入成本

            # 缓存读取成本 (基于 cache_n)
            cost_cache_read = (req.cache_n * matching_tier.cache_read_price) / 1_000_000
            
            # 缓外输入成本 (基于 prompt_n)
            cost_prompt = (req.prompt_n * matching_tier.input_price) / 1_000_000
            
            # 输出内容成本 (基于 output_tokens)
            cost_output = (req.output_tokens * matching_tier.output_price) / 1_000_000
            
            # 缓存写入成本 (基于 output_tokens，因为输出内容被写入缓存)
            cost_cache_write = (req.output_tokens * matching_tier.cache_write_price) / 1_000_000
            
            total_cost = cost_cache_read + cost_prompt + cost_output + cost_cache_write
            
        else:
            # **不支持缓存的计费逻辑**
            # 成本 = 总输入成本 + 总输出成本
            cost_input = (req.input_tokens * matching_tier.input_price) / 1_000_000
            cost_output = (req.output_tokens * matching_tier.output_price) / 1_000_000
            total_cost = cost_input + cost_output

        return total_cost

    async def _calculate_cost_vectorized(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        【高性能向量化版本】为DataFrame中的所有请求批量计算成本。
        此函数取代了逐行 apply 的低效方法，是本次优化的核心。

        Args:
            df: 包含请求数据的DataFrame，必须有 'model_name' 列。

        Returns:
            带有 'cost' 列的原始DataFrame。
        """
        if df.empty:
            df['cost'] = 0.0
            return df

        # 步骤 1: 一次性并发获取所有相关模型的计费配置，减少I/O次数
        model_names = df['model_name'].unique()
        billing_tasks = [asyncio.to_thread(self.monitor.get_model_billing, name) for name in model_names]
        billing_results = await asyncio.gather(*billing_tasks)
        model_billings = {name: billing for name, billing in zip(model_names, billing_results)}

        all_costs = []
        # 步骤 2: 按模型分组处理。因为不同模型的计费规则不同，所以这是一个天然的分组边界。
        # 注意：我们是在遍历模型（数量少），而不是遍历行（数量巨大）。
        for model_name, group_df in df.groupby('model_name'):
            billing = model_billings.get(model_name)

            # 如果模型没有计费配置，或不使用阶梯计费，则该组所有请求成本为0
            if not billing or not billing.use_tier_pricing or not billing.tier_pricing:
                costs = np.zeros(len(group_df))
                all_costs.append(pd.Series(costs, index=group_df.index))
                continue

            # 步骤 3: 为当前模型的所有阶梯，构建向量化的"条件"和"选择"列表
            # 这是 np.select 的核心思想：condlist[i] 为真时，取 choicelist[i] 的值
            condlist = []
            choice_input_price, choice_output_price = [], []
            choice_cache_read_price, choice_cache_write_price = [], []
            choice_support_cache = []

            for tier in billing.tier_pricing:
                # 定义上限，-1 代表无穷大
                max_input = float('inf') if tier.max_input_tokens == -1 else tier.max_input_tokens
                max_output = float('inf') if tier.max_output_tokens == -1 else tier.max_output_tokens

                # 创建一个布尔 Series (True/False 数组)，代表 group_df 中哪些行匹配当前阶梯
                condition = (
                    (group_df['input_tokens'] > tier.min_input_tokens) &
                    (group_df['input_tokens'] <= max_input) &
                    (group_df['output_tokens'] > tier.min_output_tokens) &
                    (group_df['output_tokens'] <= max_output)
                )
                condlist.append(condition)

                # 将该阶梯对应的价格存入"选择"列表
                choice_input_price.append(tier.input_price)
                choice_output_price.append(tier.output_price)
                choice_cache_read_price.append(tier.cache_read_price)
                choice_cache_write_price.append(tier.cache_write_price)
                choice_support_cache.append(1 if tier.support_cache else 0)

            # 步骤 4: 使用 np.select，一次性为所有行匹配到正确的价格
            # 它会根据 condlist 中的条件，从 choice 列表中为每一行选出对应的值
            matched_input_price = np.select(condlist, choice_input_price, default=0)
            matched_output_price = np.select(condlist, choice_output_price, default=0)
            matched_cache_read_price = np.select(condlist, choice_cache_read_price, default=0)
            matched_cache_write_price = np.select(condlist, choice_cache_write_price, default=0)
            is_cache_supported = np.select(condlist, choice_support_cache, default=0)

            # 步骤 5: 纯向量化计算成本，这里全是数组/列级别的数学运算，速度极快
            # a) 不支持缓存的成本公式
            cost_no_cache = (group_df['input_tokens'] * matched_input_price +
                             group_df['output_tokens'] * matched_output_price) / 1_000_000

            # b) 支持缓存的成本公式 (使用修正后的正确逻辑)
            cost_with_cache = (group_df['cache_n'] * matched_cache_read_price +
                               group_df['prompt_n'] * matched_input_price +
                               group_df['output_tokens'] * matched_output_price +
                               group_df['output_tokens'] * matched_cache_write_price) / 1_000_000

            # 步骤 6: 使用 np.where 根据是否支持缓存，从两种计算结果中选择最终成本
            final_costs = np.where(is_cache_supported == 1, cost_with_cache, cost_no_cache)

            # 将计算结果（带索引）添加到列表中，以便最后合并
            all_costs.append(pd.Series(final_costs, index=group_df.index))

        # 步骤 7: 合并所有模型分组的成本计算结果，并赋值给 df['cost']
        if all_costs:
            df['cost'] = pd.concat(all_costs)
        else:
            df['cost'] = 0.0

        return df

    # 在 APIServer 类中，使用这个更简洁的版本
    async def _get_enriched_requests_dataframe(self, start_time: float, end_time: float) -> pd.DataFrame:
        """
        【最终简化版】并发获取所有需要追踪的模型的请求数据，并用模型模式(mode)丰富数据。
        职责：只获取数据，不处理计费。
        """
        async def get_model_requests_with_mode(model_name: str):
            """并发获取单个模型的请求数据并添加模式信息"""
            try:
                mode = self.config_manager.get_model_mode(model_name)
                if not self.config_manager.should_track_tokens_for_mode(mode):
                    return []
                
                # 直接在线程中执行数据库查询
                requests = await asyncio.wait_for(
                    asyncio.to_thread(self.monitor.get_model_requests, model_name, start_time, end_time),
                    timeout=15.0
                )
                # 使用列表推导式高效地为每条记录添加 model_name 和 model_mode
                return [dict(req.__dict__, model_name=model_name, model_mode=mode) for req in requests]
            except asyncio.TimeoutError:
                logger.warning(f"[API_SERVER] 获取模型 {model_name} 请求数据超时")
                return []
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型 {model_name} 请求数据失败: {e}")
                return []

        # 获取所有需要追踪的模型名称
        model_names_to_track = [
            name for name in self.config_manager.get_model_names()
            if self.config_manager.should_track_tokens_for_mode(self.config_manager.get_model_mode(name))
        ]

        if not model_names_to_track:
            return pd.DataFrame()

        # 创建并执行并发任务
        tasks = [get_model_requests_with_mode(model_name) for model_name in model_names_to_track]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 合并结果
        all_requests_data = []
        for result in results:
            if isinstance(result, Exception):
                # 错误已在子任务中记录，这里可以跳过
                continue
            all_requests_data.extend(result)

        if not all_requests_data:
            return pd.DataFrame()
        
        return pd.DataFrame(all_requests_data)

        # 获取模型列表并过滤掉不需要追踪的模型，减少并发任务数
        all_model_names = self.config_manager.get_model_names()
        tracked_models = []
        for model_name in all_model_names:
            try:
                mode = self.config_manager.get_model_mode(model_name)
                if self.config_manager.should_track_tokens_for_mode(mode):
                    tracked_models.append(model_name)
            except Exception as e:
                logger.warning(f"[API_SERVER] 获取模型 {model_name} 模式失败，跳过: {e}")

        if not tracked_models:
            return pd.DataFrame()

        # 使用信号量控制并发数量，避免过多并发请求
        semaphore = asyncio.Semaphore(5)  # 最多5个并发请求

        async def limited_get_model_data(model_name: str):
            async with semaphore:
                return await get_model_requests_with_mode_and_billing(model_name)

        # 创建并发任务
        tasks = [limited_get_model_data(model_name) for model_name in tracked_models]

        # 并发执行所有任务，设置整体超时
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30.0  # 整体超时30秒
            )
        except asyncio.TimeoutError:
            logger.error("[API_SERVER] 获取所有模型数据整体超时")
            return pd.DataFrame()

        # 合并结果，进行更精细的错误处理
        all_requests = []
        success_count = 0
        error_count = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                error_count += 1
                logger.error(f"[API_SERVER] 模型 {tracked_models[i]} 数据获取任务异常: {result}")
                continue

            if result:  # 如果有数据
                success_count += 1
                all_requests.extend(result)

        logger.debug(f"[API_SERVER] 数据获取完成: 成功 {success_count}/{len(tracked_models)} 个模型, "
                    f"失败 {error_count} 个, 总请求记录 {len(all_requests)} 条")

        if not all_requests:
            return pd.DataFrame()

        # 转换为DataFrame，使用更高效的方式
        return pd.DataFrame(all_requests)

    def _setup_routes(self):
        """设置基础路由"""

        @self.app.get("/v1/models", response_class=JSONResponse)
        async def list_models():
            return self.model_controller.get_model_list()

        @self.app.get("/")
        async def root():
            return {"message": "LLM-Manager API Server", "version": "1.0.0", "models_url": "/v1/models"}

        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "models_count": len(self.model_controller.models_state), "running_models": len([s for s in self.model_controller.models_state.values() if s['status'] == 'routing'])}

        @self.app.post("/api/models/{model_alias}/start")
        async def start_model_api(model_alias: str):
            try:
                model_name = self.config_manager.resolve_primary_name(model_alias)
                success, message = await asyncio.to_thread(self.model_controller.start_model, model_name)
                return {"success": success, "message": message}
            except KeyError:
                return {"success": False, "message": f"模型别名 '{model_alias}' 未找到"}
            except Exception as e:
                return {"success": False, "message": str(e)}

        @self.app.post("/api/models/{model_alias}/stop")
        async def stop_model_api(model_alias: str):
            try:
                model_name = self.config_manager.resolve_primary_name(model_alias)
                success, message = await asyncio.to_thread(self.model_controller.stop_model, model_name)
                return {"success": success, "message": message}
            except KeyError:
                return {"success": False, "message": f"模型别名 '{model_alias}' 未找到"}
            except Exception as e:
                return {"success": False, "message": str(e)}

        @self.app.get("/api/models/{model_alias}/logs/stream")
        async def stream_model_logs(model_alias: str):
            try:
                model_name = self.config_manager.resolve_primary_name(model_alias)
                if model_name not in self.model_controller.models_state:
                    return JSONResponse(status_code=404, content={"error": f"模型 '{model_alias}' 不存在"})
                model_status = self.model_controller.models_state[model_name].get('status')
                if model_status not in ['routing', 'starting', 'init_script', 'health_check']:
                    return JSONResponse(status_code=400, content={"error": f"模型 '{model_alias}' 未启动或已停止 (当前状态: {model_status})"})
                historical_logs = self.model_controller.get_model_logs(model_name)

                async def log_stream_generator():
                    for log_entry in historical_logs:
                        yield f"data: {json.dumps({'type': 'historical', 'log': log_entry}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0.01)
                    yield f"data: {json.dumps({'type': 'historical_complete'}, ensure_ascii=False)}\n\n"
                    subscriber_queue = self.model_controller.subscribe_to_model_logs(model_name)
                    try:
                        while True:
                            try:
                                log_entry = await asyncio.to_thread(subscriber_queue.get, timeout=1.0)
                                if log_entry is None:
                                    yield f"data: {json.dumps({'type': 'stream_end'}, ensure_ascii=False)}\n\n"
                                    break
                                yield f"data: {json.dumps({'type': 'realtime', 'log': log_entry}, ensure_ascii=False)}\n\n"
                            except queue.Empty:
                                continue
                            except Exception as e:
                                logger.error(f"流式日志推送错误: {e}")
                                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                                break
                    finally:
                        self.model_controller.unsubscribe_from_model_logs(model_name, subscriber_queue)
                return StreamingResponse(log_stream_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"})
            except KeyError:
                return JSONResponse(status_code=404, content={"error": f"模型别名 '{model_alias}' 未找到"})
            except Exception as e:
                logger.error(f"流式日志接口错误: {e}")
                return JSONResponse(status_code=500, content={"error": f"服务器内部错误: {str(e)}"})

        @self.app.post("/api/models/restart-autostart")
        async def restart_autostart_models():
            try:
                logger.info("[API_SERVER] 通过API重启所有autostart模型...")
                await asyncio.to_thread(self.model_controller.unload_all_models)
                await asyncio.sleep(2)

                # 并发启动所有autostart模型
                async def start_autostart_model_async(model_name: str):
                    """并发启动单个autostart模型"""
                    if not self.config_manager.is_auto_start(model_name):
                        return model_name, False, "模型未配置为自动启动"

                    try:
                        success, message = await asyncio.wait_for(
                            asyncio.to_thread(self.model_controller.start_model, model_name),
                            timeout=30.0  # 模型启动可能需要较长时间
                        )
                        return model_name, success, message
                    except asyncio.TimeoutError:
                        logger.warning(f"[API_SERVER] 启动模型 {model_name} 超时")
                        return model_name, False, "启动模型超时"
                    except Exception as e:
                        logger.error(f"[API_SERVER] 启动模型 {model_name} 失败: {e}")
                        return model_name, False, str(e)

                # 获取需要启动的模型
                autostart_models = [
                    model_name for model_name in self.config_manager.get_model_names()
                    if self.config_manager.is_auto_start(model_name)
                ]

                if not autostart_models:
                    return {"success": True, "message": "没有配置autostart模型", "started_models": []}

                # 创建并发任务
                tasks = [start_autostart_model_async(model_name) for model_name in autostart_models]

                # 并发执行所有任务
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 处理结果
                started_models = []
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"[API_SERVER] 模型启动任务异常: {result}")
                        continue

                    model_name, success, message = result
                    if success:
                        started_models.append(model_name)
                    else:
                        logger.warning(f"[API_SERVER] 自动启动模型 {model_name} 失败: {message}")

                return {"success": True, "message": f"已重启 {len(started_models)} 个autostart模型", "started_models": started_models}
            except Exception as e:
                logger.error(f"[API_SERVER] 重启autostart模型失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.post("/api/models/stop-all")
        async def stop_all_models():
            try:
                logger.info("[API_SERVER] 通过API关闭所有模型...")
                await asyncio.to_thread(self.model_controller.unload_all_models)
                return {"success": True, "message": "所有模型已关闭"}
            except Exception as e:
                logger.error(f"[API_SERVER] 关闭所有模型失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.get("/api/devices/info")
        async def get_device_info():
            try:
                device_plugins = self.model_controller.plugin_manager.get_all_device_plugins()

                async def get_single_device_info(device_name: str, device_plugin):
                    """异步获取单个设备信息，带超时和错误处理"""
                    try:
                        # 使用 asyncio.wait_for 设置超时，并在线程池中执行IO操作
                        is_online = await asyncio.wait_for(
                            asyncio.to_thread(device_plugin.is_online),
                            timeout=15.0
                        )

                        device_info = await asyncio.wait_for(
                            asyncio.to_thread(device_plugin.get_devices_info),
                            timeout=15.0
                        )

                        return device_name, {"online": is_online, "info": device_info}
                    except asyncio.TimeoutError:
                        logger.warning(f"[API_SERVER] 获取设备 {device_name} 信息超时")
                        return device_name, {"online": False, "error": "获取设备信息超时"}
                    except Exception as e:
                        logger.error(f"[API_SERVER] 获取设备 {device_name} 信息失败: {e}")
                        return device_name, {"online": False, "error": str(e)}

                # 创建所有设备的异步任务
                tasks = [
                    get_single_device_info(device_name, device_plugin)
                    for device_name, device_plugin in device_plugins.items()
                ]

                # 并发执行所有任务
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 处理结果
                devices_info = {}
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"[API_SERVER] 设备信息获取任务异常: {result}")
                        continue

                    device_name, device_data = result
                    devices_info[device_name] = device_data

                return {"success": True, "devices": devices_info}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取设备信息失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.get("/api/logs/stats")
        async def get_log_stats():
            try:
                return {"success": True, "stats": self.model_controller.get_log_stats()}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取日志统计失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.post("/api/logs/{model_alias}/clear/{keep_minutes}")
        async def clear_model_logs(model_alias: str, keep_minutes: int = 0):
            try:
                model_name = self.config_manager.resolve_primary_name(model_alias)
                if keep_minutes == 0:
                    self.model_controller.log_manager.clear_logs(model_name)
                    message = f"模型 '{model_alias}' 所有日志已清空"
                else:
                    removed_count = self.model_controller.log_manager.cleanup_old_logs(model_name, keep_minutes)
                    message = f"模型 '{model_alias}' 已清理 {keep_minutes} 分钟前的日志，删除 {removed_count} 条"
                return {"success": True, "message": message}
            except KeyError:
                return {"success": False, "message": f"模型别名 '{model_alias}' 未找到"}
            except Exception as e:
                logger.error(f"[API_SERVER] 清理日志失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.get("/api/models/{model_alias}/info")
        async def get_model_info(model_alias: str):
            try:
                if model_alias == "all-models":
                    # 并发获取所有模型状态和待处理请求数
                    async def get_model_info_async(model_name: str, model_status: dict):
                        """并发获取单个模型的完整信息"""
                        try:
                            pending_requests = self.api_router.pending_requests.get(model_name, 0)
                            return model_name, {**model_status, "pending_requests": pending_requests}
                        except Exception as e:
                            logger.error(f"[API_SERVER] 获取模型 {model_name} 信息失败: {e}")
                            return model_name, {**model_status, "pending_requests": 0, "error": str(e)}

                    # 获取所有模型状态
                    all_models_status = await asyncio.wait_for(
                        asyncio.to_thread(self.model_controller.get_all_models_status),
                        timeout=15.0
                    )

                    # 创建并发任务
                    tasks = [
                        get_model_info_async(model_name, model_status)
                        for model_name, model_status in all_models_status.items()
                    ]

                    # 并发执行所有任务
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # 处理结果
                    all_models_info = {}
                    for result in results:
                        if isinstance(result, Exception):
                            logger.error(f"[API_SERVER] 模型信息获取任务异常: {result}")
                            continue

                        model_name, model_info = result
                        all_models_info[model_name] = model_info

                    return {
                        "success": True,
                        "models": all_models_info,
                        "total_models": len(all_models_info),
                        "running_models": len([m for m in all_models_info.values() if m["status"] == "routing"]),
                        "total_pending_requests": sum(m["pending_requests"] for m in all_models_info.values())
                    }
                else:
                    model_name = self.config_manager.resolve_primary_name(model_alias)
                    if model_name not in self.model_controller.models_state:
                        return JSONResponse(status_code=404, content={"success": False, "error": f"模型 '{model_alias}' 不存在"})
                    all_models_status = self.model_controller.get_all_models_status()
                    model_status = all_models_status.get(model_name)
                    if not model_status:
                        return JSONResponse(status_code=404, content={"success": False, "error": f"模型 '{model_alias}' 状态信息未找到"})
                    return {"success": True, "model": {**model_status, "pending_requests": self.api_router.pending_requests.get(model_name, 0)}}
            except KeyError:
                return JSONResponse(status_code=404, content={"success": False, "error": f"模型别名 '{model_alias}' 未找到"})
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型信息失败: {e}")
                return JSONResponse(status_code=500, content={"success": False, "error": f"服务器内部错误: {str(e)}"})

        @self.app.get("/api/metrics/throughput/{start_time}/{end_time}/{n_samples}")
        async def get_throughput(start_time: float, end_time: float, n_samples: int):
            """【全新重构】获取真实的、基于请求时长的吞吐量趋势，并按模型模式分解"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样点数"})

                interval = (end_time - start_time) / n_samples
                tracked_modes = self.config_manager.get_token_tracker_modes()
                point_template = {"input_tokens_per_sec": 0.0, "output_tokens_per_sec": 0.0, "total_tokens_per_sec": 0.0, "cache_hit_tokens_per_sec": 0.0, "cache_miss_tokens_per_sec": 0.0}

                # 1. 使用辅助函数获取包含模式和新时间戳信息的DataFrame
                df = await self._get_enriched_requests_dataframe(start_time, end_time)

                # 2. 处理无数据的情况
                if df.empty:
                    time_points = [{"timestamp": start_time + (i + 0.5) * interval, "data": point_template} for i in range(n_samples)]
                    mode_breakdown = {mode: list(time_points) for mode in tracked_modes}
                    return {"success": True, "data": {"time_points": time_points, "mode_breakdown": mode_breakdown}}
                
                # 3. 【核心修改】计算每个请求的真实吞吐量
                df['duration'] = df['end_time'] - df['start_time']
                # 将小于1毫秒的持续时间视为1毫秒，以防止出现过大的TPS值和除零错误
                df['safe_duration'] = np.maximum(df['duration'], 0.0001)

                df['input_tps'] = df['input_tokens'] / df['safe_duration']
                df['output_tps'] = df['output_tokens'] / df['safe_duration']
                df['total_tps'] = (df['input_tokens'] + df['output_tokens']) / df['safe_duration']
                df['cache_hit_tps'] = df['cache_n'] / df['safe_duration']
                df['cache_miss_tps'] = df['prompt_n'] / df['safe_duration']

                # 4. 按请求结束时间进行分桶
                df['bin_index'] = np.clip(np.floor((df['end_time'] - start_time) / interval), 0, n_samples - 1).astype(int)
                
                # 5. 【核心修改】聚合时计算平均值 (mean)，而不是错误地求和
                agg_cols = {
                    "input_tps": "mean",
                    "output_tps": "mean",
                    "total_tps": "mean",
                    "cache_hit_tps": "mean",
                    "cache_miss_tps": "mean"
                }
                overall_agg = df.groupby('bin_index').agg(agg_cols)
                mode_agg = df.groupby(['bin_index', 'model_mode']).agg(agg_cols)
                
                # 6. 格式化输出
                time_points = []
                mode_breakdown = {mode: [] for mode in tracked_modes}

                for i in range(n_samples):
                    ts = start_time + (i + 0.5) * interval
                    
                    # 总体数据点
                    if i in overall_agg.index:
                        row = overall_agg.loc[i]
                        time_points.append({"timestamp": ts, "data": {
                            "input_tokens_per_sec": row["input_tps"],
                            "output_tokens_per_sec": row["output_tps"],
                            "total_tokens_per_sec": row["total_tps"],
                            "cache_hit_tokens_per_sec": row["cache_hit_tps"],
                            "cache_miss_tokens_per_sec": row["cache_miss_tps"]
                        }})
                    else:
                        time_points.append({"timestamp": ts, "data": point_template})
                    
                    # 按模式分解的数据点
                    for mode in tracked_modes:
                        if (i, mode) in mode_agg.index:
                            row = mode_agg.loc[(i, mode)]
                            mode_breakdown[mode].append({"timestamp": ts, "data": {
                                "input_tokens_per_sec": row["input_tps"],
                                "output_tokens_per_sec": row["output_tps"],
                                "total_tokens_per_sec": row["total_tps"],
                                "cache_hit_tokens_per_sec": row["cache_hit_tps"],
                                "cache_miss_tokens_per_sec": row["cache_miss_tps"]
                            }})
                        else:
                            mode_breakdown[mode].append({"timestamp": ts, "data": point_template})

                return {"success": True, "data": {"time_points": time_points, "mode_breakdown": mode_breakdown}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取吞吐量趋势失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/metrics/throughput/current-session")
        async def get_current_session_total():
            """【性能优化版】获取本次运行总消耗"""
            try:
                program_runtime = await asyncio.wait_for(
                    asyncio.to_thread(self.monitor.get_program_runtime, 1),
                    timeout=15.0
                )
                default_data = {"total_cost_yuan": 0.0, "total_input_tokens": 0, "total_output_tokens": 0,
                                "total_cache_n": 0, "total_prompt_n": 0, "session_start_time": None}
                if not program_runtime:
                    return {"success": True, "data": {"session_total": default_data}}

                start_time = program_runtime[0].start_time

                # 1. 【性能优化】使用我们的数据获取函数，一次性并发获取所有模型在会话期间的请求数据
                df = await self._get_enriched_requests_dataframe(start_time, time.time())

                if df.empty:
                    default_data["session_start_time"] = start_time
                    return {"success": True, "data": {"session_total": default_data}}

                # 2. 【核心优化】调用向量化函数，一次性计算所有成本
                df = await self._calculate_cost_vectorized(df)

                # 3. 【优化点】使用Pandas的内置聚合函数进行总计，速度极快
                summary = {
                    "total_input_tokens": int(df['input_tokens'].sum()),
                    "total_output_tokens": int(df['output_tokens'].sum()),
                    "total_cache_n": int(df['cache_n'].sum()),
                    "total_prompt_n": int(df['prompt_n'].sum()),
                    "total_cost_yuan": round(df['cost'].sum(), 6),
                    "session_start_time": start_time
                }

                return {"success": True, "data": {"session_total": summary}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取本次运行总消耗失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/usage-summary/{start_time}/{end_time}")
        async def get_usage_summary(start_time: float, end_time: float):
            """【全新升级】获取在指定时间范围内，各个模型模式消耗的Token总和与资金成本总和。"""
            try:
                if start_time >= end_time:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围"})

                # 1. 使用辅助函数并发获取所有相关请求数据
                df = await self._get_enriched_requests_dataframe(start_time, end_time)

                # 2. 初始化结果结构
                tracked_modes = self.config_manager.get_token_tracker_modes()
                mode_summary = {mode: {"total_tokens": 0, "total_cost": 0.0} for mode in tracked_modes}
                overall_summary = {"total_tokens": 0, "total_cost": 0.0}

                # 3. 如果在指定时间范围内没有任何请求，直接返回初始化的零值结果
                if df.empty:
                    return {
                        "success": True,
                        "data": {
                            "mode_summary": mode_summary,
                            "overall_summary": overall_summary
                        }
                    }

                # 4. 【性能优化】调用向量化函数，一次性计算所有成本
                df['total_tokens'] = df['input_tokens'] + df['output_tokens']
                df = await self._calculate_cost_vectorized(df)

                # 5. 按模型模式(model_mode)对总Token和成本进行分组求和
                agg_cols = {"total_tokens": "sum", "cost": "sum"}
                mode_agg = df.groupby('model_mode').agg(agg_cols)

                # 6. 将计算出的结果填充到结果字典中
                for mode, row in mode_agg.iterrows():
                    if mode in mode_summary:
                        mode_summary[mode]['total_tokens'] = int(row['total_tokens'])
                        mode_summary[mode]['total_cost'] = round(row['cost'], 6)

                # 7. 计算所有模式的Token和成本总和
                overall_summary['total_tokens'] = int(mode_agg['total_tokens'].sum())
                overall_summary['total_cost'] = round(mode_agg['cost'].sum(), 6)

                # 8. 返回最终的汇总数据
                return {
                    "success": True,
                    "data": {
                        "mode_summary": mode_summary,
                        "overall_summary": overall_summary
                    }
                }
            except Exception as e:
                logger.error(f"[API_SERVER] 获取使用量汇总失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/token-trends/{start_time}/{end_time}/{n_samples}")
        async def get_token_trends(start_time: float, end_time: float, n_samples: int):
            """【逻辑不变】获取Token消耗趋势，并按模型模式分解。此接口返回时间序列的总量数据。"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样点数"})

                interval = (end_time - start_time) / n_samples
                tracked_modes = self.config_manager.get_token_tracker_modes()
                point_template = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0}

                df = await self._get_enriched_requests_dataframe(start_time, end_time)

                if df.empty:
                    time_points = [{"timestamp": start_time + (i + 0.5) * interval, "data": point_template} for i in range(n_samples)]
                    mode_breakdown = {mode: list(time_points) for mode in tracked_modes}
                    return {"success": True, "data": {"time_points": time_points, "mode_breakdown": mode_breakdown}}
                
                df['bin_index'] = np.clip(np.floor((df['end_time'] - start_time) / interval), 0, n_samples - 1).astype(int)
                agg_cols = {"input_tokens": "sum", "output_tokens": "sum", "cache_n": "sum", "prompt_n": "sum"}
                
                overall_agg = df.groupby('bin_index')[list(agg_cols.keys())].agg(agg_cols)
                mode_agg = df.groupby(['bin_index', 'model_mode'])[list(agg_cols.keys())].agg(agg_cols)
                
                time_points = []
                mode_breakdown = {mode: [] for mode in tracked_modes}

                for i in range(n_samples):
                    ts = start_time + (i + 0.5) * interval
                    
                    if i in overall_agg.index:
                        row = overall_agg.loc[i]
                        time_points.append({"timestamp": ts, "data": {
                            "input_tokens": int(row["input_tokens"]),
                            "output_tokens": int(row["output_tokens"]),
                            "total_tokens": int(row["input_tokens"] + row["output_tokens"]),
                            "cache_hit_tokens": int(row["cache_n"]),
                            "cache_miss_tokens": int(row["prompt_n"])
                        }})
                    else:
                        time_points.append({"timestamp": ts, "data": point_template})
                    
                    for mode in tracked_modes:
                        if (i, mode) in mode_agg.index:
                            row = mode_agg.loc[(i, mode)]
                            mode_breakdown[mode].append({"timestamp": ts, "data": {
                                "input_tokens": int(row["input_tokens"]),
                                "output_tokens": int(row["output_tokens"]),
                                "total_tokens": int(row["input_tokens"] + row["output_tokens"]),
                                "cache_hit_tokens": int(row["cache_n"]),
                                "cache_miss_tokens": int(row["prompt_n"])
                            }})
                        else:
                             mode_breakdown[mode].append({"timestamp": ts, "data": point_template})

                return {"success": True, "data": {"time_points": time_points, "mode_breakdown": mode_breakdown}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取Token趋势失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/cost-trends/{start_time}/{end_time}/{n_samples}")
        async def get_cost_trends(start_time: float, end_time: float, n_samples: int):
            """【逻辑不变】获取成本趋势，并按模型模式分解。此接口返回时间序列的总量数据。"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样点数"})

                interval = (end_time - start_time) / n_samples
                tracked_modes = self.config_manager.get_token_tracker_modes()
                point_template = {"cost": 0.0}

                # 1. 获取 enriched DataFrame
                df = await self._get_enriched_requests_dataframe(start_time, end_time)
                
                if df.empty:
                    time_points = [{"timestamp": start_time + (i + 0.5) * interval, "data": point_template} for i in range(n_samples)]
                    mode_breakdown = {mode: list(time_points) for mode in tracked_modes}
                    return {"success": True, "data": {"time_points": time_points, "mode_breakdown": mode_breakdown}}

                # 2. 【性能优化】调用向量化函数，一次性计算所有成本
                df = await self._calculate_cost_vectorized(df)

                # 3. 聚合
                df['bin_index'] = np.clip(np.floor((df['end_time'] - start_time) / interval), 0, n_samples - 1).astype(int)
                overall_agg = df.groupby('bin_index')['cost'].sum()
                mode_agg = df.groupby(['bin_index', 'model_mode'])['cost'].sum()

                # 4. 格式化输出
                time_points = []
                mode_breakdown = {mode: [] for mode in tracked_modes}

                for i in range(n_samples):
                    ts = start_time + (i + 0.5) * interval
                    cost = round(overall_agg.get(i, 0.0), 6)
                    time_points.append({"timestamp": ts, "data": {"cost": cost}})
                    
                    for mode in tracked_modes:
                        mode_cost = round(mode_agg.get((i, mode), 0.0), 6)
                        mode_breakdown[mode].append({"timestamp": ts, "data": {"cost": mode_cost}})

                return {"success": True, "data": {"time_points": time_points, "mode_breakdown": mode_breakdown}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取成本趋势失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/model-stats/{model_name_alias}/{start_time}/{end_time}/{n_samples}")
        async def get_model_stats(model_name_alias: str, start_time: float, end_time: float, n_samples: int):
            """【逻辑不变】获取单模型在指定时间范围内的详细统计数据。"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样点数"})

                model_name = self.config_manager.resolve_primary_name(model_name_alias)
                
                # 获取模型请求数据并创建DataFrame
                requests = self.monitor.get_model_requests(model_name, start_time, end_time)

                total_duration = end_time - start_time
                interval = total_duration / n_samples

                summary_template = {"total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0, "total_cache_n": 0, "total_prompt_n": 0, "total_cost": 0, "request_count": 0}
                point_template = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0, "cost": 0.0}
                time_points = [{"timestamp": start_time + (i + 0.5) * interval, "data": point_template.copy()} for i in range(n_samples)]

                if not requests:
                    return {"success": True, "data": {"model_name": model_name, "summary": summary_template, "time_points": time_points}}

                # 创建DataFrame并添加模型名称列（用于向量化计算）
                df = pd.DataFrame([req.__dict__ for req in requests])
                df['model_name'] = model_name
                df['total_tokens'] = df['input_tokens'] + df['output_tokens']

                # 【性能优化】调用向量化函数，一次性计算所有成本
                df = await self._calculate_cost_vectorized(df)

                summary = {
                    "total_input_tokens": int(df['input_tokens'].sum()),
                    "total_output_tokens": int(df['output_tokens'].sum()),
                    "total_tokens": int(df['total_tokens'].sum()),
                    "total_cache_n": int(df['cache_n'].sum()),
                    "total_prompt_n": int(df['prompt_n'].sum()),
                    "total_cost": round(df['cost'].sum(), 6),
                    "request_count": len(df)
                }

                bin_indices = np.floor((df['end_time'] - start_time) / interval)
                df['bin_index'] = np.clip(bin_indices, 0, n_samples - 1).astype(int)

                agg_cols = {"input_tokens": "sum", "output_tokens": "sum", "total_tokens": "sum", "cache_n": "sum", "prompt_n": "sum", "cost": "sum"}
                time_agg_df = df.groupby('bin_index')[list(agg_cols.keys())].agg(agg_cols)
                
                final_time_points = []
                for i in range(n_samples):
                    ts = start_time + (i + 0.5) * interval
                    if i in time_agg_df.index:
                        point_data = time_agg_df.loc[i]
                        final_time_points.append({
                            "timestamp": ts,
                            "data": {
                                "input_tokens": int(point_data["input_tokens"]),
                                "output_tokens": int(point_data["output_tokens"]),
                                "total_tokens": int(point_data["total_tokens"]),
                                "cache_hit_tokens": int(point_data["cache_n"]),
                                "cache_miss_tokens": int(point_data["prompt_n"]),
                                "cost": round(point_data["cost"], 6)
                            }
                        })
                    else:
                        final_time_points.append({"timestamp": ts, "data": point_template})

                return {"success": True, "data": {"model_name": model_name, "summary": summary, "time_points": final_time_points}}
            except KeyError:
                return JSONResponse(status_code=404, content={"success": False, "error": f"模型别名 '{model_name_alias}' 未找到"})
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型统计数据失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/billing/models/{model_name}/pricing")
        async def get_model_pricing(model_name: str):
            """获取模型计费配置"""
            try:
                # 解析模型名称
                primary_name = self.config_manager.resolve_primary_name(model_name)

                billing = self.monitor.get_model_billing(primary_name)
                if not billing:
                    return {"success": False, "error": f"模型 '{model_name}' 的计费配置不存在"}

                return {
                    "success": True,
                    "data": {
                        "model_name": primary_name,
                        "pricing_type": "tier" if billing.use_tier_pricing else "hourly",
                        "tier_pricing": [
                            {
                                "tier_index": tier.tier_index,
                                "min_input_tokens": tier.min_input_tokens,
                                "max_input_tokens": tier.max_input_tokens,
                                "min_output_tokens": tier.min_output_tokens,
                                "max_output_tokens": tier.max_output_tokens,
                                "input_price": tier.input_price,
                                "output_price": tier.output_price,
                                "support_cache": tier.support_cache,
                                "cache_write_price": tier.cache_write_price,
                                "cache_read_price": tier.cache_read_price
                            }
                            for tier in billing.tier_pricing
                        ],
                        "hourly_price": billing.hourly_price
                    }
                }

            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型计费配置失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.post("/api/billing/models/{model_name}/pricing/tier")
        async def set_tier_pricing(model_name: str, pricing_data: dict):
            """设置模型阶梯计费"""
            try:
                # 解析模型名称
                primary_name = self.config_manager.resolve_primary_name(model_name)

                # 验证数据
                required_fields = ["tier_index", "min_input_tokens", "max_input_tokens",
                                 "min_output_tokens", "max_output_tokens", "input_price",
                                 "output_price", "support_cache", "cache_write_price", "cache_read_price"]

                for field in required_fields:
                    if field not in pricing_data:
                        return {"success": False, "error": f"缺少必要字段: {field}"}

                # 设置阶梯计费
                tier_data = [
                    pricing_data["tier_index"],
                    pricing_data["min_input_tokens"],
                    pricing_data["max_input_tokens"],
                    pricing_data["min_output_tokens"],
                    pricing_data["max_output_tokens"],
                    pricing_data["input_price"],
                    pricing_data["output_price"],
                    pricing_data["support_cache"],
                    pricing_data["cache_write_price"],
                    pricing_data["cache_read_price"]
                ]

                self.monitor.update_tier_pricing(primary_name, tier_data)
                self.monitor.update_billing_method(primary_name, True)

                return {
                    "success": True,
                    "message": f"模型 '{model_name}' 阶梯计费配置已更新"
                }

            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 设置阶梯计费失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.post("/api/billing/models/{model_name}/pricing/hourly")
        async def set_hourly_pricing(model_name: str, pricing_data: dict):
            """设置模型按时计费"""
            try:
                # 解析模型名称
                primary_name = self.config_manager.resolve_primary_name(model_name)

                # 验证数据
                if "hourly_price" not in pricing_data:
                    return {"success": False, "error": "缺少必要字段: hourly_price"}

                # 设置按时计费
                hourly_price = float(pricing_data["hourly_price"])
                self.monitor.update_hourly_price(primary_name, hourly_price)
                self.monitor.update_billing_method(primary_name, False)

                return {
                    "success": True,
                    "message": f"模型 '{model_name}' 按时计费配置已更新"
                }

            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 设置按时计费失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/data/models/orphaned")
        async def get_orphaned_models():
            """获取孤立模型数据（配置中不存在但数据库中有数据的模型）"""
            try:
                import os
                import sqlite3

                # 获取配置中的模型名称
                configured_models = set(self.config_manager.get_model_names())

                # 获取数据库中的所有模型表
                orphaned_models = []

                if os.path.exists(self.monitor.db_path):
                    conn = sqlite3.connect(self.monitor.db_path)
                    cursor = conn.cursor()

                    # 获取所有表名
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = cursor.fetchall()

                    for table in tables:
                        table_name = table[0]
                        if table_name.endswith('_requests'):
                            # 从表名提取模型名称
                            model_name = table_name[:-9]  # 移除 '_requests' 后缀

                            # 检查是否在映射表中
                            cursor.execute('''
                                SELECT original_name FROM model_name_mapping
                                WHERE safe_name = ?
                            ''', (model_name,))
                            result = cursor.fetchone()

                            if result:
                                original_name = result[0]
                                if original_name not in configured_models:
                                    orphaned_models.append(original_name)

                    conn.close()

                return {
                    "success": True,
                    "data": {
                        "orphaned_models": orphaned_models,
                        "count": len(orphaned_models)
                    }
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取孤立模型数据失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.delete("/api/data/models/{model_name}")
        async def delete_model_data(model_name: str):
            """删除指定模型的数据"""
            try:
                # 检查模型是否在配置中
                configured_models = self.config_manager.get_model_names()
                if model_name in configured_models:
                    return {"success": False, "error": f"模型 '{model_name}' 仍在配置中，无法删除"}

                # 删除模型数据
                self.monitor.delete_model_tables(model_name)

                return {
                    "success": True,
                    "message": f"模型 '{model_name}' 的数据已删除"
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 删除模型数据失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/data/storage/stats")
        async def get_storage_stats():
            """获取存储统计信息"""
            try:
                import os
                import sqlite3

                stats = {
                    "database_exists": False,
                    "database_size_mb": 0,
                    "total_models_with_data": 0,
                    "total_requests": 0,
                    "models_data": {}
                }

                if os.path.exists(self.monitor.db_path):
                    stats["database_exists"] = True
                    stats["database_size_mb"] = round(os.path.getsize(self.monitor.db_path) / (1024 * 1024), 2)

                    # 并发获取所有模型统计信息
                    async def get_model_storage_stats_async(model_name: str):
                        """并发获取单个模型的存储统计信息"""
                        try:
                            # 在线程池中执行数据库操作
                            result = await asyncio.wait_for(
                                asyncio.to_thread(self._get_single_model_storage_stats, model_name),
                                timeout=15.0
                            )
                            return model_name, result
                        except asyncio.TimeoutError:
                            logger.warning(f"[API_SERVER] 获取模型 {model_name} 存储统计超时")
                            return model_name, {
                                "request_count": 0,
                                "has_runtime_data": False,
                                "has_billing_data": False
                            }
                        except Exception as e:
                            logger.error(f"[API_SERVER] 获取模型 {model_name} 存储统计失败: {e}")
                            return model_name, {
                                "request_count": 0,
                                "has_runtime_data": False,
                                "has_billing_data": False
                            }

                    # 获取配置中的模型名称
                    configured_models = self.config_manager.get_model_names()

                    # 创建并发任务
                    tasks = [get_model_storage_stats_async(model_name) for model_name in configured_models]

                    # 并发执行所有任务
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # 处理结果
                    total_requests = 0
                    for result in results:
                        if isinstance(result, Exception):
                            logger.error(f"[API_SERVER] 模型存储统计任务异常: {result}")
                            continue

                        model_name, model_stats = result
                        stats["models_data"][model_name] = model_stats
                        total_requests += model_stats["request_count"]

                    stats["total_models_with_data"] = len([m for m in stats["models_data"].values() if m["request_count"] > 0])
                    stats["total_requests"] = total_requests

                return {
                    "success": True,
                    "data": stats
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取存储统计失败: {e}")
                return {"success": False, "error": str(e)}

        def _get_single_model_storage_stats(self, model_name: str):
            """同步获取单个模型的存储统计信息（在线程池中执行）"""
            import sqlite3

            safe_name = self.monitor.get_model_safe_name(model_name)
            if not safe_name:
                return {
                    "request_count": 0,
                    "has_runtime_data": False,
                    "has_billing_data": False
                }

            try:
                conn = sqlite3.connect(self.monitor.db_path)
                cursor = conn.cursor()

                # 获取请求数量
                cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_requests")
                request_count = cursor.fetchone()[0]

                # 检查是否有运行时间数据
                cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_runtime")
                has_runtime_data = cursor.fetchone()[0] > 0

                # 检查是否有计费数据
                cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_tier_pricing")
                has_billing_data = cursor.fetchone()[0] > 0

                conn.close()

                return {
                    "request_count": request_count,
                    "has_runtime_data": has_runtime_data,
                    "has_billing_data": has_billing_data
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型 {model_name} 存储统计详情失败: {e}")
                return {
                    "request_count": 0,
                    "has_runtime_data": False,
                    "has_billing_data": False
                }

        @self.app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
        async def handle_all_requests(request: Request, path: str):
            """统一请求处理器"""
            return await self.api_router.route_request(request, path, self.token_tracker)


    def run(self, host: Optional[str] = None, port: Optional[int] = None):
        """运行API服务器"""
        import uvicorn
        if host is None or port is None:
            api_cfg = self.config_manager.get_openai_config()
            host = host or api_cfg['host']
            port = port or api_cfg['port']
        logger.info(f"[API_SERVER] 统一API接口将在 http://{host}:{port} 上启动")
        uvicorn.run(self.app, host=host, port=port, log_level="warning")


_app_instance: Optional[FastAPI] = None
_server_instance: Optional[APIServer] = None

def run_api_server(config_manager: ConfigManager, host: Optional[str] = None, port: Optional[int] = None):
    """运行API服务器"""
    global _app_instance, _server_instance
    _server_instance = APIServer(config_manager)
    _app_instance = _server_instance.app
    _server_instance.run(host, port)