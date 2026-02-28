from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from typing import List, Optional
import os
import json
import time
import asyncio
import queue

import pandas as pd
import numpy as np

from utils.logger import get_logger
from core.config_manager import ConfigManager
from core.model_controller import ModelController
from core.data_manager import Monitor
from core.api_router import APIRouter, TokenTracker

logger = get_logger(__name__)


class APIServer:
    """
    API服务器
    负责FastAPI应用管理、路由配置以及数据统计接口的具体实现。
    """

    def __init__(self, config_manager: ConfigManager, model_controller: ModelController,
                 app_version: str):
        self.config_manager = config_manager
        self.model_controller = model_controller
        self.app_version = app_version
        self.monitor = Monitor()
        self.token_tracker = TokenTracker(self.monitor, self.config_manager)
        self.api_router = APIRouter(self.config_manager, self.model_controller)
        
        self.model_controller.set_api_router(self.api_router)
        self.app = FastAPI(title="LLM-Manager API", version=app_version)
        
        self._setup_routes()

        # 自动启动逻辑由 main.py 中的 Application 类控制，此处无需调用，避免竞态条件
        # self.model_controller.start_auto_start_models()

        logger.info("[API_SERVER] 初始化完成")
        logger.debug("[API_SERVER] Token跟踪功能已激活")

    async def _calculate_cost_vectorized(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        批量计算请求成本（高性能、财务级安全向量化版本）。

        优化特性：
        1. 修复了缓存写入计费逻辑：写入缓存的Token数等于未命中缓存的计算输入（prompt_n）。
        2. 高精度保证：在行级别先计算未除以 1,000,000 的微成本（raw_cost），
           避免极小浮点数相加产生的低位截断（精度漂移）。
        3. 细粒度计算：保留每次请求的真实成本，而非简单按阶梯平摊，确保后续单条流水查询绝对准确。

        Args:
            df: 包含请求数据的DataFrame，必须包含 'model_name' 列。
            groupby_cols: (可选) 兼容接口，此版本直接向量化计算无需在内部预聚合。

        Returns:
            pd.DataFrame: 包含 'cost' 和 'raw_cost' 列的DataFrame。
                         - raw_cost: 微成本（float64，未除以1,000,000）
                         - cost: 最终成本（元，已除以1,000,000）
        """
        if df.empty:
            df['raw_cost'] = 0.0
            df['cost'] = 0.0
            return df

        # 1. 批量并发获取当前 DataFrame 中涉及的所有模型的计费配置
        model_names = df['model_name'].unique()
        billing_tasks =[asyncio.to_thread(self.monitor.get_model_billing, name) for name in model_names]
        billing_results = await asyncio.gather(*billing_tasks)
        model_billings = {name: billing for name, billing in zip(model_names, billing_results)}

        all_raw_costs =[]

        # 2. 按模型分组，准备应用计费规则
        for model_name, group_df in df.groupby('model_name'):
            billing = model_billings.get(model_name)

            # 无计费配置，或者未开启按量阶梯计费，成本直接为 0
            if not billing or not billing.use_tier_pricing or not billing.tier_pricing:
                raw_costs = np.zeros(len(group_df), dtype=np.float64)
                all_raw_costs.append(pd.Series(raw_costs, index=group_df.index))
                continue

            # 3. 提取阶梯配置，构建 numpy 的条件向量(condlist)和选择向量(choicelist)
            condlist =[]
            choice_input_price = []
            choice_output_price =[]
            choice_cache_read_price = []
            choice_cache_write_price = []
            choice_support_cache =[]

            for tier in billing.tier_pricing:
                max_input = float('inf') if tier.max_input_tokens == -1 else tier.max_input_tokens
                max_output = float('inf') if tier.max_output_tokens == -1 else tier.max_output_tokens

                # 动态决定区间开闭：最小值为 0 时闭区间，否则开区间
                input_condition = (group_df['input_tokens'] >= tier.min_input_tokens) if tier.min_input_tokens == 0 else (group_df['input_tokens'] > tier.min_input_tokens)
                output_condition = (group_df['output_tokens'] >= tier.min_output_tokens) if tier.min_output_tokens == 0 else (group_df['output_tokens'] > tier.min_output_tokens)

                condition = (
                    input_condition &
                    (group_df['input_tokens'] <= max_input) &
                    output_condition &
                    (group_df['output_tokens'] <= max_output)
                )
                
                condlist.append(condition)
                choice_input_price.append(tier.input_price)
                choice_output_price.append(tier.output_price)
                choice_cache_read_price.append(tier.cache_read_price)
                choice_cache_write_price.append(tier.cache_write_price)
                choice_support_cache.append(1 if tier.support_cache else 0)

            # 4. 利用 np.select 瞬间将单价映射到每一行 (C 语言底层执行，极速映射)
            matched_input_price = np.select(condlist, choice_input_price, default=0.0)
            matched_output_price = np.select(condlist, choice_output_price, default=0.0)
            matched_cache_read_price = np.select(condlist, choice_cache_read_price, default=0.0)
            matched_cache_write_price = np.select(condlist, choice_cache_write_price, default=0.0)
            is_cache_supported = np.select(condlist, choice_support_cache, default=0)

            # 5. 【核心业务逻辑修正】逐行计算高精度微成本
            
            # (A) 不支持缓存的模型计费公式：直接 输入 * 输入价 + 输出 * 输出价
            cost_no_cache = (
                group_df['input_tokens'] * matched_input_price +
                group_df['output_tokens'] * matched_output_price
            )

            # (B) 支持缓存的模型计费公式
            # 修正点：将 matched_cache_write_price 挂载在 prompt_n(缓存未命中) 上
            cost_with_cache = (
                group_df['cache_n'] * matched_cache_read_price +      # 命中部分：读缓存费
                group_df['prompt_n'] * matched_input_price +          # 未命中部分：计算输入费
                group_df['prompt_n'] * matched_cache_write_price +    # 未命中部分：写缓存费 <--- 修复
                group_df['output_tokens'] * matched_output_price      # 生成部分：输出费
            )

            # 根据阶梯配置是否支持缓存，选取对应数组的结果
            final_raw_costs = np.where(is_cache_supported == 1, cost_with_cache, cost_no_cache)
            
            # 将该模型下这批请求的微成本拼接到总列表中 (保持高精度 float64，防止带有小数的定价被截断)
            all_raw_costs.append(pd.Series(final_raw_costs, index=group_df.index))

        # 6. 将处理好的微成本数据绑定回原始 DataFrame
        if all_raw_costs:
            df['raw_cost'] = pd.concat(all_raw_costs)
        else:
            df['raw_cost'] = 0.0

        # 7. 生成可供前端展示或单条查询的最终成本 (除以百万)
        # （注：外层 API 仍可针对 raw_cost 进行 sum() 操作然后再除，效果等价且彻底无漂移）
        df['cost'] = df['raw_cost'] / 1_000_000

        return df

    async def _get_enriched_requests_dataframe(self, start_time: float, end_time: float) -> pd.DataFrame:
        """
        并发获取指定时间段内的请求数据，并补充模型模式(mode)信息。
        """
        async def get_model_requests_with_mode(model_name: str):
            try:
                mode = self.config_manager.get_model_mode(model_name)
                if not self.config_manager.should_track_tokens_for_mode(mode):
                    return []
                
                requests = await asyncio.wait_for(
                    asyncio.to_thread(self.monitor.get_model_requests, model_name, start_time, end_time),
                    timeout=15.0
                )
                return [dict(req.__dict__, model_name=model_name, model_mode=mode) for req in requests]
            except asyncio.TimeoutError:
                logger.warning(f"[API_SERVER] 获取模型 {model_name} 请求数据超时")
                return []
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型 {model_name} 请求数据失败: {e}")
                return []

        model_names_to_track = [
            name for name in self.config_manager.get_model_names()
            if self.config_manager.should_track_tokens_for_mode(self.config_manager.get_model_mode(name))
        ]

        if not model_names_to_track:
            return pd.DataFrame()

        tasks = [get_model_requests_with_mode(model_name) for model_name in model_names_to_track]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_requests_data = []
        for result in results:
            if isinstance(result, Exception):
                continue
            all_requests_data.extend(result)

        if not all_requests_data:
            return pd.DataFrame()
        
        return pd.DataFrame(all_requests_data)

    def _calculate_hourly_cost_trends(
        self,
        start_time: float,
        end_time: float,
        n_samples: int,
        hourly_models: List[str]
    ) -> tuple[np.ndarray, dict]:
        """
        计算按时计费模型的时间序列成本。

        Returns:
            tuple: (总成本数组, 按模式分解的成本字典)
        """
        if n_samples <= 0 or not hourly_models:
            return np.zeros(1), {}

        interval = (end_time - start_time) / n_samples
        total_costs_per_bucket = np.zeros(n_samples)
        tracked_modes = self.config_manager.get_token_tracker_modes()
        mode_costs_per_bucket = {mode: np.zeros(n_samples) for mode in tracked_modes}

        def calculate_overlap_duration(start1: float, end1: float, start2: float, end2: float) -> float:
            overlap_start = max(start1, start2)
            overlap_end = min(end1, end2)
            return max(0, overlap_end - overlap_start)

        for model_name in hourly_models:
            try:
                billing_cfg = self.monitor.get_model_billing(model_name)
                if not billing_cfg or billing_cfg.hourly_price <= 0:
                    continue

                hourly_rate_per_sec = billing_cfg.hourly_price / 3600.0
                model_mode = self.config_manager.get_model_mode(model_name)
                runtime_sessions = self.monitor.get_model_runtime_in_range(model_name, start_time, end_time)

                for session in runtime_sessions:
                    session_start = session.start_time
                    session_end = session.end_time if session.end_time is not None else time.time()

                    for i in range(n_samples):
                        bucket_start = start_time + i * interval
                        bucket_end = bucket_start + interval
                        overlap_seconds = calculate_overlap_duration(session_start, session_end, bucket_start, bucket_end)

                        if overlap_seconds > 0:
                            cost_in_bucket = overlap_seconds * hourly_rate_per_sec
                            total_costs_per_bucket[i] += cost_in_bucket
                            if model_mode in mode_costs_per_bucket:
                                mode_costs_per_bucket[model_mode][i] += cost_in_bucket
            except Exception as e:
                logger.warning(f"[API_SERVER] 计算按时计费模型 {model_name} 成本失败: {e}")
                continue

        return total_costs_per_bucket, mode_costs_per_bucket

    def _setup_routes(self):
        """配置API路由"""

        @self.app.get("/v1/models", response_class=JSONResponse)
        async def list_models():
            return self.model_controller.get_model_list()

        @self.app.get("/api/info")
        async def api_info():
            return {"message": "LLM-Manager API Server", "version": self.app_version, "models_url": "/v1/models"}

        @self.app.get("/api/health")
        async def health_check():
            return {
                "status": "healthy",
                "role": "Manager",
                "models_count": len(self.model_controller.models_state),
                "running_models": len([s for s in self.model_controller.models_state.values() if s['status'] == 'routing'])
            }

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
                    # 推送历史日志
                    for log_entry in historical_logs:
                        yield f"data: {json.dumps({'type': 'historical', 'log': log_entry}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0.01)
                    yield f"data: {json.dumps({'type': 'historical_complete'}, ensure_ascii=False)}\n\n"
                    
                    # 订阅实时日志
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
                                logger.error(f"[API_SERVER] 流式日志推送异常: {e}")
                                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                                break
                    finally:
                        self.model_controller.unsubscribe_from_model_logs(model_name, subscriber_queue)

                return StreamingResponse(log_stream_generator(), media_type="text/event-stream", headers={
                    "Cache-Control": "no-cache", "Connection": "keep-alive", 
                    "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"
                })
            except KeyError:
                return JSONResponse(status_code=404, content={"error": f"模型别名 '{model_alias}' 未找到"})
            except Exception as e:
                logger.error(f"[API_SERVER] 流式日志接口错误: {e}")
                return JSONResponse(status_code=500, content={"error": f"服务器内部错误: {str(e)}"})

        @self.app.post("/api/models/restart-autostart")
        async def restart_autostart_models():
            try:
                logger.info("[API_SERVER] 正在重启所有Autostart模型...")
                await asyncio.to_thread(self.model_controller.unload_all_models)
                await asyncio.sleep(2)

                async def start_autostart_model_async(model_name: str):
                    if not self.config_manager.is_auto_start(model_name):
                        return model_name, False, "模型未配置为自动启动"
                    try:
                        success, message = await asyncio.wait_for(
                            asyncio.to_thread(self.model_controller.start_model, model_name),
                            timeout=30.0
                        )
                        return model_name, success, message
                    except asyncio.TimeoutError:
                        logger.warning(f"[API_SERVER] 启动模型 {model_name} 超时")
                        return model_name, False, "启动超时"
                    except Exception as e:
                        logger.error(f"[API_SERVER] 启动模型 {model_name} 失败: {e}")
                        return model_name, False, str(e)

                autostart_models = [
                    model_name for model_name in self.config_manager.get_model_names()
                    if self.config_manager.is_auto_start(model_name)
                ]

                if not autostart_models:
                    return {"success": True, "message": "无Autostart模型配置", "started_models": []}

                tasks = [start_autostart_model_async(model_name) for model_name in autostart_models]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                started_models = []
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    model_name, success, message = result
                    if success:
                        started_models.append(model_name)
                    else:
                        logger.warning(f"[API_SERVER] 自动启动模型 {model_name} 失败: {message}")

                return {
                    "success": True, 
                    "message": f"已重启 {len(started_models)} 个模型", 
                    "started_models": started_models
                }
            except Exception as e:
                logger.error(f"[API_SERVER] 重启Autostart模型失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.post("/api/models/stop-all")
        async def stop_all_models():
            try:
                logger.info("[API_SERVER] 关闭所有模型...")
                await asyncio.to_thread(self.model_controller.unload_all_models)
                return {"success": True, "message": "所有模型已关闭"}
            except Exception as e:
                logger.error(f"[API_SERVER] 关闭所有模型失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.get("/api/devices/info")
        async def get_device_info():
            try:
                self.model_controller.plugin_manager.on_api_request()
                devices_info = self.model_controller.plugin_manager.get_device_status_snapshot()
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
                    async def get_model_info_async(model_name: str, model_status: dict):
                        try:
                            pending_requests = self.api_router.pending_requests.get(model_name, 0)
                            return model_name, {**model_status, "pending_requests": pending_requests}
                        except Exception as e:
                            logger.error(f"[API_SERVER] 获取模型 {model_name} 信息失败: {e}")
                            return model_name, {**model_status, "pending_requests": 0, "error": str(e)}

                    all_models_status = await asyncio.wait_for(
                        asyncio.to_thread(self.model_controller.get_all_models_status),
                        timeout=15.0
                    )

                    tasks = [get_model_info_async(name, status) for name, status in all_models_status.items()]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    all_models_info = {}
                    for result in results:
                        if isinstance(result, Exception):
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
                        return JSONResponse(status_code=404, content={"success": False, "error": "模型状态未找到"})
                    
                    return {"success": True, "model": {**model_status, "pending_requests": self.api_router.pending_requests.get(model_name, 0)}}
            except KeyError:
                return JSONResponse(status_code=404, content={"success": False, "error": f"模型别名 '{model_alias}' 未找到"})
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型信息失败: {e}")
                return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

        @self.app.get("/api/metrics/throughput/{start_time}/{end_time}/{n_samples}")
        async def get_throughput(start_time: float, end_time: float, n_samples: int):
            """获取基于真实请求时长的吞吐量趋势"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样数"})

                interval = (end_time - start_time) / n_samples
                tracked_modes = self.config_manager.get_token_tracker_modes()
                point_template = {"input_tokens_per_sec": 0.0, "output_tokens_per_sec": 0.0, "total_tokens_per_sec": 0.0, "cache_hit_tokens_per_sec": 0.0, "cache_miss_tokens_per_sec": 0.0}

                df = await self._get_enriched_requests_dataframe(start_time, end_time)

                if df.empty:
                    time_points = [{"timestamp": start_time + (i + 0.5) * interval, "data": point_template} for i in range(n_samples)]
                    mode_breakdown = {mode: list(time_points) for mode in tracked_modes}
                    return {"success": True, "data": {"time_points": time_points, "mode_breakdown": mode_breakdown}}
                
                df['duration'] = df['end_time'] - df['start_time']
                df['safe_duration'] = np.maximum(df['duration'], 0.0001)

                df['input_tps'] = df['input_tokens'] / df['safe_duration']
                df['output_tps'] = df['output_tokens'] / df['safe_duration']
                df['total_tps'] = (df['input_tokens'] + df['output_tokens']) / df['safe_duration']
                df['cache_hit_tps'] = df['cache_n'] / df['safe_duration']
                df['cache_miss_tps'] = df['prompt_n'] / df['safe_duration']

                df['bin_index'] = np.clip(np.floor((df['end_time'] - start_time) / interval), 0, n_samples - 1).astype(int)
                
                agg_cols = {
                    "input_tps": "mean", "output_tps": "mean", "total_tps": "mean",
                    "cache_hit_tps": "mean", "cache_miss_tps": "mean"
                }
                overall_agg = df.groupby('bin_index').agg(agg_cols)
                mode_agg = df.groupby(['bin_index', 'model_mode']).agg(agg_cols)
                
                time_points = []
                mode_breakdown = {mode: [] for mode in tracked_modes}

                for i in range(n_samples):
                    ts = start_time + (i + 0.5) * interval
                    
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
                logger.error(f"[API_SERVER] 获取吞吐量数据失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/metrics/throughput/current-session")
        async def get_current_session_total():
            """获取本次运行总消耗（混合计费）"""
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
                end_time = time.time()

                all_model_names = self.config_manager.get_model_names()
                billing_configs = {}
                for name in all_model_names:
                    try:
                        billing_configs[name] = self.monitor.get_model_billing(name)
                    except Exception:
                        billing_configs[name] = None

                tiered_models = [name for name, cfg in billing_configs.items() if cfg and cfg.use_tier_pricing]
                hourly_models = [name for name, cfg in billing_configs.items() if cfg and not cfg.use_tier_pricing]

                summary = {
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_cache_n": 0,
                    "total_prompt_n": 0,
                    "total_cost_yuan": 0.0,
                    "session_start_time": start_time
                }

                # 按量计费模型
                if tiered_models:
                    df_all_requests = await self._get_enriched_requests_dataframe(start_time, end_time)
                    if not df_all_requests.empty:
                        df_tiered = df_all_requests[df_all_requests['model_name'].isin(tiered_models)]
                        if not df_tiered.empty:
                            df_tiered = await self._calculate_cost_vectorized(df_tiered)
                            summary["total_input_tokens"] += int(df_tiered['input_tokens'].sum())
                            summary["total_output_tokens"] += int(df_tiered['output_tokens'].sum())
                            summary["total_cache_n"] += int(df_tiered['cache_n'].sum())
                            summary["total_prompt_n"] += int(df_tiered['prompt_n'].sum())
                            summary["total_cost_yuan"] += round(df_tiered['cost'].sum(), 6)

                # 按时计费模型
                if hourly_models:
                    hourly_total_costs, _ = await asyncio.to_thread(
                        self._calculate_hourly_cost_trends,
                        start_time, end_time, 1, hourly_models
                    )
                    summary["total_cost_yuan"] += round(float(hourly_total_costs[0]), 6)

                return {"success": True, "data": {"session_total": summary}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取本次运行总消耗失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/usage-summary/{start_time}/{end_time}")
        async def get_usage_summary(start_time: float, end_time: float):
            """获取指定时间范围内的模型消耗汇总（混合计费）"""
            try:
                if start_time >= end_time:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围"})

                all_model_names = self.config_manager.get_model_names()
                billing_configs = {}
                for name in all_model_names:
                    try:
                        billing_configs[name] = self.monitor.get_model_billing(name)
                    except Exception:
                        billing_configs[name] = None

                tiered_models = [name for name, cfg in billing_configs.items() if cfg and cfg.use_tier_pricing]
                hourly_models = [name for name, cfg in billing_configs.items() if cfg and not cfg.use_tier_pricing]

                tracked_modes = self.config_manager.get_token_tracker_modes()
                mode_summary = {mode: {"total_tokens": 0, "total_cost": 0.0} for mode in tracked_modes}
                overall_summary = {"total_tokens": 0, "total_cost": 0.0}

                # 按量计费处理
                if tiered_models:
                    df_all_requests = await self._get_enriched_requests_dataframe(start_time, end_time)
                    if not df_all_requests.empty:
                        df_tiered = df_all_requests[df_all_requests['model_name'].isin(tiered_models)]
                        if not df_tiered.empty:
                            df_tiered['total_tokens'] = df_tiered['input_tokens'] + df_tiered['output_tokens']
                            df_tiered = await self._calculate_cost_vectorized(df_tiered)

                            agg_cols = {"total_tokens": "sum", "cost": "sum"}
                            tiered_mode_agg = df_tiered.groupby('model_mode').agg(agg_cols)

                            for mode, row in tiered_mode_agg.iterrows():
                                if mode in mode_summary:
                                    mode_summary[mode]['total_tokens'] += int(row['total_tokens'])
                                    mode_summary[mode]['total_cost'] += round(row['cost'], 6)

                            overall_summary['total_tokens'] += int(tiered_mode_agg['total_tokens'].sum())
                            overall_summary['total_cost'] += round(tiered_mode_agg['cost'].sum(), 6)

                # 按时计费处理
                if hourly_models:
                    hourly_total_costs, hourly_mode_costs = await asyncio.to_thread(
                        self._calculate_hourly_cost_trends,
                        start_time, end_time, 1, hourly_models
                    )
                    overall_summary['total_cost'] += round(float(hourly_total_costs[0]), 6)
                    for mode in tracked_modes:
                        if mode in hourly_mode_costs:
                            mode_summary[mode]['total_cost'] += round(float(hourly_mode_costs[mode][0]), 6)

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
            """获取Token消耗趋势"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样数"})

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
            """获取成本趋势（混合计费）"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样数"})

                interval = (end_time - start_time) / n_samples
                tracked_modes = self.config_manager.get_token_tracker_modes()

                all_model_names = self.config_manager.get_model_names()
                billing_configs = {}
                for name in all_model_names:
                    try:
                        billing_configs[name] = self.monitor.get_model_billing(name)
                    except Exception:
                        billing_configs[name] = None

                tiered_models = [name for name, cfg in billing_configs.items() if cfg and cfg.use_tier_pricing]
                hourly_models = [name for name, cfg in billing_configs.items() if cfg and not cfg.use_tier_pricing]

                total_costs = np.zeros(n_samples)
                mode_costs = {mode: np.zeros(n_samples) for mode in tracked_modes}

                # 按量计费
                if tiered_models:
                    df_all_requests = await self._get_enriched_requests_dataframe(start_time, end_time)
                    if not df_all_requests.empty:
                        df_tiered = df_all_requests[df_all_requests['model_name'].isin(tiered_models)]
                        if not df_tiered.empty:
                            df_tiered = await self._calculate_cost_vectorized(df_tiered)
                            df_tiered['bin_index'] = np.clip(np.floor((df_tiered['end_time'] - start_time) / interval), 0, n_samples - 1).astype(int)

                            tiered_overall_agg = df_tiered.groupby('bin_index')['cost'].sum()
                            tiered_mode_agg = df_tiered.groupby(['bin_index', 'model_mode'])['cost'].sum()

                            for i in range(n_samples):
                                total_costs[i] += tiered_overall_agg.get(i, 0.0)
                                for mode in tracked_modes:
                                    mode_costs[mode][i] += tiered_mode_agg.get((i, mode), 0.0)

                # 按时计费
                if hourly_models:
                    hourly_total_costs, hourly_mode_costs = await asyncio.to_thread(
                        self._calculate_hourly_cost_trends,
                        start_time, end_time, n_samples, hourly_models
                    )
                    total_costs += hourly_total_costs
                    for mode in tracked_modes:
                        if mode in hourly_mode_costs:
                            mode_costs[mode] += hourly_mode_costs[mode]

                time_points = []
                mode_breakdown = {mode: [] for mode in tracked_modes}

                for i in range(n_samples):
                    ts = start_time + (i + 0.5) * interval
                    cost = round(float(total_costs[i]), 6)
                    time_points.append({"timestamp": ts, "data": {"cost": cost}})

                    for mode in tracked_modes:
                        mode_cost = round(float(mode_costs[mode][i]), 6)
                        mode_breakdown[mode].append({"timestamp": ts, "data": {"cost": mode_cost}})

                return {"success": True, "data": {"time_points": time_points, "mode_breakdown": mode_breakdown}}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取成本趋势失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/model-stats/{model_name_alias}/{start_time}/{end_time}/{n_samples}")
        async def get_model_stats(model_name_alias: str, start_time: float, end_time: float, n_samples: int):
            """获取单模型详细统计数据（混合计费）"""
            try:
                if start_time >= end_time or n_samples <= 0:
                    return JSONResponse(status_code=400, content={"success": False, "error": "无效的时间范围或采样数"})

                model_name = self.config_manager.resolve_primary_name(model_name_alias)

                try:
                    billing_cfg = self.monitor.get_model_billing(model_name)
                except Exception:
                    billing_cfg = None

                total_duration = end_time - start_time
                interval = total_duration / n_samples

                summary_template = {"total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0, "total_cache_n": 0, "total_prompt_n": 0, "total_cost": 0, "request_count": 0}
                point_template = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0, "cost": 0.0}
                time_points = [{"timestamp": start_time + (i + 0.5) * interval, "data": point_template.copy()} for i in range(n_samples)]

                if not billing_cfg:
                    return {"success": True, "data": {"model_name": model_name, "summary": summary_template, "time_points": time_points}}

                if billing_cfg.use_tier_pricing:
                    # 按量计费
                    requests = self.monitor.get_model_requests(model_name, start_time, end_time)
                    if not requests:
                        return {"success": True, "data": {"model_name": model_name, "summary": summary_template, "time_points": time_points}}

                    df = pd.DataFrame([req.__dict__ for req in requests])
                    df['model_name'] = model_name
                    df['total_tokens'] = df['input_tokens'] + df['output_tokens']
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

                    df['bin_index'] = np.clip(np.floor((df['end_time'] - start_time) / interval), 0, n_samples - 1).astype(int)
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
                                    "cache_miss_tokens": int(point_data["prompt_n"] - point_data["cache_n"]),
                                    "cost": round(float(point_data["cost"]), 6)
                                }
                            })
                        else:
                            final_time_points.append({"timestamp": ts, "data": point_template.copy()})

                else:
                    # 按时计费
                    hourly_total_costs, _ = await asyncio.to_thread(
                        self._calculate_hourly_cost_trends,
                        start_time, end_time, 1, [model_name]
                    )

                    summary = {
                        "total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0,
                        "total_cache_n": 0, "total_prompt_n": 0,
                        "total_cost": round(float(hourly_total_costs[0]), 6),
                        "request_count": 0
                    }

                    hourly_costs_by_time, _ = await asyncio.to_thread(
                        self._calculate_hourly_cost_trends,
                        start_time, end_time, n_samples, [model_name]
                    )

                    final_time_points = []
                    for i in range(n_samples):
                        ts = start_time + (i + 0.5) * interval
                        cost = round(float(hourly_costs_by_time[i]), 6)
                        final_time_points.append({
                            "timestamp": ts,
                            "data": {
                                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                                "cache_hit_tokens": 0, "cache_miss_tokens": 0,
                                "cost": cost
                            }
                        })

                return {"success": True, "data": {"model_name": model_name, "summary": summary, "time_points": final_time_points}}
            except KeyError:
                return JSONResponse(status_code=404, content={"success": False, "error": f"模型别名 '{model_name_alias}' 未找到"})
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型统计数据失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/billing/models/{model_name}/pricing")
        async def get_model_pricing(model_name: str):
            try:
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
        
        @self.app.post("/api/billing/models/{model_name}/pricing/set/{method}")
        async def set_billing_method(model_name: str, method: str):
            try:
                primary_name = self.config_manager.resolve_primary_name(model_name)
                if method not in ["tier", "hourly"]:
                    return {"success": False, "error": "无效的计费类型，请使用 'tier' 或 'hourly'"}

                use_tier_pricing = (method == "tier")
                self.monitor.update_billing_method(primary_name, use_tier_pricing)
                return {
                    "success": True,
                    "message": f"模型 '{model_name}' 的计费方式已更新为 '{method}'"
                }
            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 设置计费方式失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.post("/api/billing/models/{model_name}/pricing/tier")
        async def set_tier_pricing(model_name: str, pricing_data: dict):
            try:
                primary_name = self.config_manager.resolve_primary_name(model_name)
                required_fields = ["tier_index", "min_input_tokens", "max_input_tokens",
                                "min_output_tokens", "max_output_tokens", "input_price",
                                "output_price", "support_cache", "cache_write_price", "cache_read_price"]

                for field in required_fields:
                    if field not in pricing_data:
                        return {"success": False, "error": f"缺少必要字段: {field}"}

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
                self.monitor.upsert_tier_pricing(primary_name, tier_data)
                return {"success": True, "message": f"模型 '{model_name}' 阶梯计费配置已更新"}
            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 设置阶梯计费失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.delete("/api/billing/models/{model_name}/pricing/tier/{tier_index}")
        async def delete_tier_pricing(model_name: str, tier_index: int):
            try:
                primary_name = self.config_manager.resolve_primary_name(model_name)
                self.monitor.delete_and_reindex_tier(primary_name, tier_index)
                return {"success": True, "message": f"模型 '{model_name}' 的计费档位 {tier_index} 已删除"}
            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except ValueError as ve:
                return {"success": False, "error": str(ve)}
            except Exception as e:
                logger.error(f"[API_SERVER] 删除计费档位失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.post("/api/billing/models/{model_name}/pricing/hourly")
        async def set_hourly_pricing(model_name: str, pricing_data: dict):
            try:
                primary_name = self.config_manager.resolve_primary_name(model_name)
                if "hourly_price" not in pricing_data:
                    return {"success": False, "error": "缺少必要字段: hourly_price"}

                hourly_price = float(pricing_data["hourly_price"])
                self.monitor.update_hourly_price(primary_name, hourly_price)
                return {"success": True, "message": f"模型 '{model_name}' 按时计费配置已更新"}
            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 设置按时计费失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/data/models/orphaned")
        async def get_orphaned_models():
            """获取孤立模型数据（配置中不存在但数据库中有数据）"""
            try:
                orphaned_models_list = await asyncio.to_thread(self.monitor.get_orphaned_models)
                return {
                    "success": True,
                    "data": {
                        "orphaned_models": orphaned_models_list,
                        "count": len(orphaned_models_list)
                    }
                }
            except Exception as e:
                logger.error(f"[API_SERVER] 获取孤立模型数据失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.delete("/api/data/models/{model_name}")
        async def delete_model_data(model_name: str):
            """删除指定孤立模型的数据"""
            try:
                if model_name in self.config_manager.get_model_names():
                    return {"success": False, "error": f"模型 '{model_name}' 仍在配置中，无法删除"}

                await asyncio.to_thread(self.monitor.delete_model_tables, model_name)
                return {"success": True, "message": f"模型 '{model_name}' 的数据已成功删除"}
            except Exception as e:
                logger.error(f"[API_SERVER] 删除模型数据失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.app.get("/api/data/storage/stats")
        async def get_storage_stats():
            """获取存储统计信息"""
            try:
                stats = {"database_exists": False, "database_size_mb": 0, "total_models_with_data": 0, "total_requests": 0, "models_data": {}}

                if not os.path.exists(self.monitor.db_path):
                    return {"success": True, "data": stats}

                stats["database_exists"] = True
                stats["database_size_mb"] = round(os.path.getsize(self.monitor.db_path) / (1024 * 1024), 2)

                all_db_models = await asyncio.to_thread(self.monitor.get_all_db_models)
                if not all_db_models:
                    return {"success": True, "data": stats}

                async def get_model_storage_stats_async(model_name: str):
                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(self.monitor.get_single_model_storage_stats, model_name),
                            timeout=15.0
                        )
                        return model_name, result
                    except asyncio.TimeoutError:
                        logger.warning(f"[API_SERVER] 获取模型 {model_name} 存储统计超时")
                        return model_name, {"request_count": 0, "has_runtime_data": False, "has_billing_data": False, "error": "timeout"}
                    except Exception as e:
                        logger.error(f"[API_SERVER] 获取模型 {model_name} 存储统计失败: {e}")
                        return model_name, {"request_count": 0, "has_runtime_data": False, "has_billing_data": False, "error": str(e)}

                tasks = [get_model_storage_stats_async(model_name) for model_name in all_db_models]
                results = await asyncio.gather(*tasks)

                total_requests = 0
                models_with_data_count = 0
                for model_name, model_stats in results:
                    stats["models_data"][model_name] = model_stats
                    if model_stats.get("request_count", 0) > 0:
                        total_requests += model_stats["request_count"]
                        models_with_data_count += 1
                
                stats["total_models_with_data"] = models_with_data_count
                stats["total_requests"] = total_requests

                return {"success": True, "data": stats}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取存储统计失败: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # 静态文件服务
        webui_dist_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webui', 'dist')
        logger.debug(f"[API_SERVER] WebUI路径: {os.path.abspath(webui_dist_path)}")

        @self.app.get("/", response_class=FileResponse)
        async def serve_frontend():
            """提供前端首页"""
            index_path = os.path.join(webui_dist_path, 'index.html')
            if os.path.exists(index_path):
                return FileResponse(index_path)
            else:
                logger.error(f"[API_SERVER] 前端文件缺失: {index_path}")
                return JSONResponse(status_code=404, content={"error": "前端文件未找到，请先构建前端"})

        @self.app.get("/{path:path}", response_class=FileResponse)
        async def serve_static_files(path: str):
            """提供前端静态资源"""
            file_path = os.path.join(webui_dist_path, path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                return FileResponse(file_path)
            else:
                # SPA路由回落到index.html
                index_path = os.path.join(webui_dist_path, 'index.html')
                if os.path.exists(index_path):
                    return FileResponse(index_path)
                else:
                    return JSONResponse(status_code=404, content={"error": "前端文件未找到"})

        @self.app.api_route("/{path:path}", methods=["POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
        async def handle_api_requests(request: Request, path: str):
            """通用API请求代理"""
            return await self.api_router.route_request(request, path, self.token_tracker)


    def run(self, host: Optional[str] = None, port: Optional[int] = None):
        """启动API服务器"""
        import uvicorn
        if host is None or port is None:
            server_cfg = self.config_manager.get_openai_config()
            host = host or server_cfg['host']
            port = port or server_cfg['port']
        logger.info(f"[API_SERVER] 服务器启动中: http://{host}:{port}")
        uvicorn.run(self.app, host=host, port=port, log_level="warning")


_app_instance: Optional[FastAPI] = None
_server_instance: Optional[APIServer] = None

def run_api_server(config_manager: ConfigManager, model_controller: ModelController,
                   app_version: str,
                   host: Optional[str] = None, port: Optional[int] = None):
    """全局启动函数"""
    global _app_instance, _server_instance
    _server_instance = APIServer(config_manager, model_controller, app_version)
    _app_instance = _server_instance.app
    _server_instance.run(host, port)