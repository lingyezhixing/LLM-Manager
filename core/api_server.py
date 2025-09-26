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
        self.token_tracker = TokenTracker(self.monitor)
        self.api_router = APIRouter(self.config_manager, self.model_controller)
        self.app = FastAPI(title="LLM-Manager API", version="1.0.0")
        self._setup_routes()
        self.model_controller.start_auto_start_models()
        logger.info("API服务器初始化完成")
        logger.debug("[API_SERVER] token跟踪功能已激活")

    def _calculate_tiered_cost_for_tokens(self, tokens_to_price: int, tiers: List[TierPricing], price_key: str) -> float:
        """
        【保留并优化】辅助函数：为单一类型的token流（如纯输出token）计算阶梯费用。
        现在它也依赖于token总量来决定阶梯，而不是自身数量。
        注意：此函数现在主要用于计算独立的 output_tokens。
        """
        cost = 0.0
        remaining_tokens_to_price = tokens_to_price
        processed_tokens_cursor = 0

        for tier in tiers:
            if remaining_tokens_to_price <= 0:
                break

            tier_start = tier.start_tokens
            # 对于最后一个阶梯，结束设为无限大
            tier_end = tier.end_tokens if tier.end_tokens > 0 else float('inf')
            
            # 计算当前阶梯与已处理token的重叠部分
            overlap_start = max(tier_start, processed_tokens_cursor)
            overlap_end = min(tier_end, processed_tokens_cursor + remaining_tokens_to_price)
            
            tokens_in_this_tier = max(0, overlap_end - overlap_start)
            
            if tokens_in_this_tier > 0:
                price_per_million = getattr(tier, price_key, 0.0)
                cost += (tokens_in_this_tier * price_per_million) / 1_000_000
                
            processed_tokens_cursor += tokens_in_this_tier
        
        return cost

    def _calculate_request_cost(self, req: ModelRequest, billing: Optional[ModelBilling]) -> float:
        """
        【全新重写】计算单个请求的成本，精确处理分阶缓存和总输入依赖。
        """
        if not billing or not billing.use_tier_pricing or not billing.tier_pricing:
            return 0.0

        total_cost = 0.0
        # 从数据库获取时已按 tier_index 排序，无需再次排序
        tiers = billing.tier_pricing
        
        # =====================================================================
        # 1. 统一计算输入成本 (Input Cost) - 这是最核心的修改
        # =====================================================================
        total_input_tokens = req.input_tokens
        remaining_cache_hits = req.cache_n
        
        # 使用一个“光标”来跟踪我们处理到了总输入的哪个位置
        processed_input_tokens = 0

        for tier in tiers:
            if processed_input_tokens >= total_input_tokens:
                break

            tier_start = tier.start_tokens
            tier_end = tier.end_tokens if tier.end_tokens > 0 else float('inf')

            # 计算总输入token落入当前阶梯的部分
            # 例如 tier 是 1000-2000，总输入是 1500，已处理 800，则此阶梯处理 1000-1500
            tokens_in_this_tier = max(0, min(total_input_tokens, tier_end) - max(processed_input_tokens, tier_start))
            
            if tokens_in_this_tier <= 0:
                continue

            # 在这个阶梯内，优先分配缓存命中部分
            cache_hits_in_this_tier = 0
            if tier.support_cache and remaining_cache_hits > 0:
                cache_hits_in_this_tier = min(tokens_in_this_tier, remaining_cache_hits)
                
                # 累加缓存成本
                cost_for_cache = (cache_hits_in_this_tier * tier.cache_hit_price_per_million) / 1_000_000
                total_cost += cost_for_cache
                
                remaining_cache_hits -= cache_hits_in_this_tier

            # 剩余部分为非缓存输入
            non_cached_in_this_tier = tokens_in_this_tier - cache_hits_in_this_tier
            if non_cached_in_this_tier > 0:
                cost_for_non_cached = (non_cached_in_this_tier * tier.input_price_per_million) / 1_000_000
                total_cost += cost_for_non_cached
            
            # 移动光标
            processed_input_tokens += tokens_in_this_tier

        # =====================================================================
        # 2. 独立计算输出成本 (Output Cost)
        # 输出的计费阶梯依赖于输出token自身的数量，与输入无关
        # =====================================================================
        if req.output_tokens > 0:
            # 我们可以复用旧的辅助函数来计算这个独立的token流
            total_cost += self._calculate_tiered_cost_for_tokens(
                req.output_tokens, tiers, 'output_price_per_million'
            )
        
        return total_cost

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
                started_models = []
                for model_name in self.config_manager.get_model_names():
                    if self.config_manager.is_auto_start(model_name):
                        success, message = await asyncio.to_thread(self.model_controller.start_model, model_name)
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
                            timeout=5.0
                        )

                        device_info = await asyncio.wait_for(
                            asyncio.to_thread(device_plugin.get_devices_info),
                            timeout=5.0
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
                    all_models_info = {}
                    all_models_status = self.model_controller.get_all_models_status()
                    for model_name, model_status in all_models_status.items():
                        all_models_info[model_name] = {**model_status, "pending_requests": self.api_router.pending_requests.get(model_name, 0)}
                    return {"success": True, "models": all_models_info, "total_models": len(all_models_info), "running_models": len([m for m in all_models_info.values() if m["status"] == "routing"]), "total_pending_requests": sum(m["pending_requests"] for m in all_models_info.values())}
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
            """【向量化重构-微调版】获取指定时间段内的吞吐量趋势"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样点数"})

                total_duration = end_time - start_time
                interval = total_duration / n_samples
                
                # 1. 一次性获取所有相关数据
                all_requests_as_dicts = [req.__dict__ for model_name in self.config_manager.get_model_names() for req in self.monitor.get_model_requests(model_name, start_time, end_time)]
                
                if not all_requests_as_dicts:
                    # 返回 n_samples 个空数据点
                    point_template = {"input_tokens_per_sec": 0, "output_tokens_per_sec": 0, "total_tokens_per_sec": 0, "cache_hit_tokens_per_sec": 0, "cache_miss_tokens_per_sec": 0}
                    time_points = [{"timestamp": start_time + (i + 1) * interval, "data": point_template} for i in range(n_samples)]
                    return {"success": True, "data": {"time_points": time_points}}

                # 2. 转换为 Pandas DataFrame
                df = pd.DataFrame(all_requests_as_dicts)

                # 3. 向量化计算分桶索引
                bin_indices = np.floor((df['timestamp'] - start_time) / interval)
                df['bin_index'] = np.clip(bin_indices, 0, n_samples - 1).astype(int)

                # 4. 使用 groupby 进行高效聚合
                agg_cols = {"input_tokens": "sum", "output_tokens": "sum", "cache_n": "sum", "prompt_n": "sum"}
                agg_df = df.groupby('bin_index')[list(agg_cols.keys())].agg(agg_cols)
                agg_df['total_tokens'] = agg_df['input_tokens'] + agg_df['output_tokens']
                
                # 5. 补全空桶并格式化输出
                all_bins_df = pd.DataFrame(index=pd.RangeIndex(start=0, stop=n_samples, name='bin_index'))
                final_agg_df = agg_df.reindex(all_bins_df.index, fill_value=0)
                
                result_points = []
                safe_interval = max(interval, 1e-9)
                
                for bin_index, row in final_agg_df.iterrows():
                    result_points.append({
                        "timestamp": start_time + (bin_index + 1) * interval,
                        "data": {
                            "input_tokens_per_sec": row["input_tokens"] / safe_interval,
                            "output_tokens_per_sec": row["output_tokens"] / safe_interval,
                            "total_tokens_per_sec": row["total_tokens"] / safe_interval,
                            "cache_hit_tokens_per_sec": row["cache_n"] / safe_interval,
                            "cache_miss_tokens_per_sec": row["prompt_n"] / safe_interval
                        }
                    })

                return {"success": True, "data": {"time_points": result_points}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取吞吐量趋势失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/metrics/throughput/current-session")
        async def get_current_session_total():
            """【已优化】获取本次运行总消耗"""
            try:
                program_runtime = self.monitor.get_program_runtime(limit=1)
                default_data = {"total_cost_yuan": 0.0, "total_input_tokens": 0, "total_output_tokens": 0, "total_cache_n": 0, "total_prompt_n": 0, "session_start_time": None}
                if not program_runtime:
                    return {"success": True, "data": {"session_total": default_data}}

                start_time = program_runtime[0].start_time
                summary = {k: v for k, v in default_data.items() if k != "session_start_time"}

                for model_name in self.config_manager.get_model_names():
                    requests = self.monitor.get_model_requests(model_name, minutes=0)
                    billing = self.monitor.get_model_billing(model_name)
                    for req in requests:
                        if req.timestamp >= start_time:
                            summary["total_input_tokens"] += req.input_tokens
                            summary["total_output_tokens"] += req.output_tokens
                            summary["total_cache_n"] += req.cache_n
                            summary["total_prompt_n"] += req.prompt_n
                            summary["total_cost_yuan"] += self._calculate_request_cost(req, billing)
                
                summary["total_cost_yuan"] = round(summary["total_cost_yuan"], 6)
                summary["session_start_time"] = start_time
                return {"success": True, "data": {"session_total": summary}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取本次运行总消耗失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/token-distribution/{start_time}/{end_time}")
        async def get_token_distribution(start_time: float, end_time: float):
            """【向量化重构】获取指定时间范围内各模型的Token分布比例"""
            try:
                if start_time >= end_time:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围"})

                # 1. 一次性获取所有模型数据，并附加上模型名称
                all_requests_with_model = []
                for model_name in self.config_manager.get_model_names():
                    requests = self.monitor.get_model_requests(model_name, start_time, end_time)
                    for req in requests:
                        req_dict = req.__dict__
                        req_dict['model_name'] = model_name
                        all_requests_with_model.append(req_dict)

                if not all_requests_with_model:
                    return {"success": True, "data": {"model_token_distribution": {}}}
                
                # 2. 转换为 DataFrame
                df = pd.DataFrame(all_requests_with_model)
                
                # 3. 计算 total_tokens 列
                df['total_tokens'] = df['input_tokens'] + df['output_tokens']
                
                # 4. 按模型名称分组并求和
                model_token_distribution = df.groupby('model_name')['total_tokens'].sum()
                
                # 5. 转换为字典格式输出
                # to_dict() 会将 Series 转换为 {model_name: total_tokens, ...}
                return {"success": True, "data": {"model_token_distribution": model_token_distribution.to_dict()}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取Token分布失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/token-trends/{start_time}/{end_time}/{n_samples}")
        async def get_token_trends(start_time: float, end_time: float, n_samples: int):
            """【向量化重构-已修正Numpy类型】获取指定时间范围内的Token消耗趋势"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样点数"})

                # 1. 获取所有数据
                all_requests_as_dicts = []
                for model_name in self.config_manager.get_model_names():
                    requests = self.monitor.get_model_requests(model_name, start_time, end_time)
                    all_requests_as_dicts.extend([req.__dict__ for req in requests])

                interval = (end_time - start_time) / n_samples
                
                if not all_requests_as_dicts:
                    # 如果没有数据，返回 n_samples 个空数据点
                    point_template = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0}
                    time_points = [{"timestamp": start_time + (i + 1) * interval, "data": point_template} for i in range(n_samples)]
                    return {"success": True, "data": {"time_points": time_points}}
                
                # 2. 转换为 DataFrame
                df = pd.DataFrame(all_requests_as_dicts)
                
                # 3. 向量化分桶
                bin_indices = np.floor((df['timestamp'] - start_time) / interval)
                df['bin_index'] = np.clip(bin_indices, 0, n_samples - 1).astype(int)
                
                # 4. 高效聚合
                agg_cols = {
                    "input_tokens": "sum", "output_tokens": "sum",
                    "cache_n": "sum", "prompt_n": "sum"
                }
                agg_df = df.groupby('bin_index')[list(agg_cols.keys())].agg(agg_cols)
                agg_df['total_tokens'] = agg_df['input_tokens'] + agg_df['output_tokens']
                
                # 5. 补全空桶并格式化输出
                all_bins_df = pd.DataFrame(index=pd.RangeIndex(start=0, stop=n_samples, name='bin_index'))
                final_agg_df = agg_df.reindex(all_bins_df.index, fill_value=0)
                
                time_points = []
                for bin_index, row in final_agg_df.iterrows():
                    time_points.append({
                        "timestamp": start_time + (bin_index + 1) * interval,
                        "data": {
                            # --- 核心修复：将 numpy 类型转换为 python 原生 int ---
                            "input_tokens": int(row["input_tokens"]),
                            "output_tokens": int(row["output_tokens"]),
                            "total_tokens": int(row["total_tokens"]),
                            "cache_hit_tokens": int(row["cache_n"]),
                            "cache_miss_tokens": int(row["prompt_n"])
                        }
                    })

                return {"success": True, "data": {"time_points": time_points}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取Token趋势失败: {e}")
                # 打印详细的 traceback 以便调试
                import traceback
                logger.error(traceback.format_exc())
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/cost-trends/{start_time}/{end_time}/{n_samples}")
        async def get_cost_trends(start_time: float, end_time: float, n_samples: int):
            """【向量化重构-微调版】获取指定时间范围内的成本趋势数据"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样点数"})
                
                total_duration = end_time - start_time
                interval = total_duration / n_samples

                all_requests_with_model = []
                model_billings = {}
                for name in self.config_manager.get_model_names():
                    model_billings[name] = self.monitor.get_model_billing(name)
                    requests = self.monitor.get_model_requests(name, start_time, end_time)
                    for req in requests:
                        req_dict = req.__dict__
                        req_dict['model_name'] = name
                        all_requests_with_model.append(req_dict)

                if not all_requests_with_model:
                    time_points = [{"timestamp": start_time + (i + 1) * interval, "cost": 0.0} for i in range(n_samples)]
                    return {"success": True, "data": {"time_points": time_points}}
                
                df = pd.DataFrame(all_requests_with_model)
                
                def calculate_cost_for_row(row):
                    temp_req = SimpleNamespace(**row.to_dict()) # 使用 SimpleNamespace 更简洁
                    billing = model_billings.get(row['model_name'])
                    return self._calculate_request_cost(temp_req, billing)

                df['cost'] = df.apply(calculate_cost_for_row, axis=1)

                bin_indices = np.floor((df['timestamp'] - start_time) / interval)
                df['bin_index'] = np.clip(bin_indices, 0, n_samples - 1).astype(int)

                cost_by_bin = df.groupby('bin_index')['cost'].sum()

                time_points = []
                for i in range(n_samples):
                    cost = cost_by_bin.get(i, 0.0)
                    time_points.append({
                        "timestamp": start_time + (i + 1) * interval,
                        "cost": round(cost, 6)
                    })

                return {"success": True, "data": {"time_points": time_points}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取成本趋势失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/model-stats/{model_name_alias}/{start_time}/{end_time}/{n_samples}")
        async def get_model_stats(model_name_alias: str, start_time: float, end_time: float, n_samples: int):
            """【向量化重构-微调版】获取单模型在指定时间范围内的详细统计数据"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样点数"})

                model_name = self.config_manager.resolve_primary_name(model_name_alias)
                
                requests_as_dicts = [req.__dict__ for req in self.monitor.get_model_requests(model_name, start_time, end_time)]
                
                total_duration = end_time - start_time
                interval = total_duration / n_samples
                
                summary_template = {"total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0, "total_cache_n": 0, "total_prompt_n": 0, "total_cost": 0, "request_count": 0}
                point_template = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0, "cost": 0.0}
                time_points = [{"timestamp": start_time + (i + 1) * interval, "data": point_template.copy()} for i in range(n_samples)]
                
                if not requests_as_dicts:
                    return {"success": True, "data": {"model_name": model_name, "summary": summary_template, "time_points": time_points}}

                df = pd.DataFrame(requests_as_dicts)
                billing = self.monitor.get_model_billing(model_name)

                def calculate_cost_for_row(row):
                    temp_req = SimpleNamespace(**row.to_dict())
                    return self._calculate_request_cost(temp_req, billing)
                df['cost'] = df.apply(calculate_cost_for_row, axis=1)
                df['total_tokens'] = df['input_tokens'] + df['output_tokens']

                summary = {
                    "total_input_tokens": int(df['input_tokens'].sum()),
                    "total_output_tokens": int(df['output_tokens'].sum()),
                    "total_tokens": int(df['total_tokens'].sum()),
                    "total_cache_n": int(df['cache_n'].sum()),
                    "total_prompt_n": int(df['prompt_n'].sum()),
                    "total_cost": round(df['cost'].sum(), 6),
                    "request_count": len(df)
                }

                bin_indices = np.floor((df['timestamp'] - start_time) / interval)
                df['bin_index'] = np.clip(bin_indices, 0, n_samples - 1).astype(int)

                agg_cols = {"input_tokens": "sum", "output_tokens": "sum", "total_tokens": "sum", "cache_n": "sum", "prompt_n": "sum", "cost": "sum"}
                time_agg_df = df.groupby('bin_index')[list(agg_cols.keys())].agg(agg_cols)
                
                final_time_points = []
                for i in range(n_samples):
                    if i in time_agg_df.index:
                        point_data = time_agg_df.loc[i]
                        final_time_points.append({
                            "timestamp": start_time + (i + 1) * interval,
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
                        final_time_points.append({"timestamp": start_time + (i + 1) * interval, "data": point_template})

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
                                "start_tokens": tier.start_tokens,
                                "end_tokens": tier.end_tokens,
                                "input_price_per_million": tier.input_price_per_million,
                                "output_price_per_million": tier.output_price_per_million,
                                "support_cache": tier.support_cache,
                                "cache_hit_price_per_million": tier.cache_hit_price_per_million
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
                required_fields = ["tier_index", "start_tokens", "end_tokens",
                                 "input_price_per_million", "output_price_per_million",
                                 "support_cache", "cache_hit_price_per_million"]

                for field in required_fields:
                    if field not in pricing_data:
                        return {"success": False, "error": f"缺少必要字段: {field}"}

                # 设置阶梯计费
                tier_data = [
                    pricing_data["tier_index"],
                    pricing_data["start_tokens"],
                    pricing_data["end_tokens"],
                    pricing_data["input_price_per_million"],
                    pricing_data["output_price_per_million"],
                    pricing_data["support_cache"],
                    pricing_data["cache_hit_price_per_million"]
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

                    conn = sqlite3.connect(self.monitor.db_path)
                    cursor = conn.cursor()

                    # 获取配置中的模型名称
                    configured_models = self.config_manager.get_model_names()

                    total_requests = 0

                    for model_name in configured_models:
                        safe_name = self.monitor.get_model_safe_name(model_name)
                        if safe_name:
                            # 获取请求数量
                            cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_requests")
                            request_count = cursor.fetchone()[0]
                            total_requests += request_count

                            stats["models_data"][model_name] = {
                                "request_count": request_count,
                                "has_runtime_data": False,
                                "has_billing_data": False
                            }

                            # 检查是否有运行时间数据
                            cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_runtime")
                            if cursor.fetchone()[0] > 0:
                                stats["models_data"][model_name]["has_runtime_data"] = True

                            # 检查是否有计费数据
                            cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_tier_pricing")
                            if cursor.fetchone()[0] > 0:
                                stats["models_data"][model_name]["has_billing_data"] = True

                    stats["total_models_with_data"] = len([m for m in stats["models_data"].values() if m["request_count"] > 0])
                    stats["total_requests"] = total_requests

                    conn.close()

                return {
                    "success": True,
                    "data": stats
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取存储统计失败: {e}")
                return {"success": False, "error": str(e)}

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