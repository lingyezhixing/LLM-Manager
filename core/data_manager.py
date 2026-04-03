#!/usr/bin/env python3
"""
LLM-Manager 数据库管理器

提供线程安全的数据库操作，用于监控模型运行状态、请求记录和计费管理。

架构说明：
- 使用统一的表结构，通过 model_id 外键关联模型
- 外键级联删除，删除模型时自动清理相关数据
- 线程本地存储，每个线程独立连接，避免并发冲突
- WAL 模式，支持读写并发
"""

import sqlite3
import threading
import time
import os
from typing import Dict, List, Optional, Union
from dataclasses import dataclass
from utils.logger import get_logger
from core.config_manager import ConfigManager

logger = get_logger(__name__)


@dataclass
class ModelRunTime:
    """模型运行时间记录"""
    id: int
    start_time: float
    end_time: float


@dataclass
class ModelRequest:
    """模型请求记录（计费核心数据）

    字段说明：
        - input_tokens: 输入token总数（用户消息 + 历史上下文）
        - output_tokens: 模型生成的输出token数量
        - cache_n: 缓存读取token数（从缓存读取，便宜）
        - prompt_n: 提示token总数（包含缓存写入，贵）
    """
    id: int
    start_time: float
    end_time: float
    input_tokens: int
    output_tokens: int
    cache_n: int
    prompt_n: int


@dataclass
class TierPricing:
    """阶梯计费配置

    价格单位：元/百万token
    区间说明：min_xxx 不含，max_xxx 含（-1表示无上限）
    """
    tier_index: int
    min_input_tokens: int
    max_input_tokens: int
    min_output_tokens: int
    max_output_tokens: int
    input_price: float
    output_price: float
    support_cache: bool
    cache_write_price: float
    cache_read_price: float


@dataclass
class ModelBilling:
    """模型计费配置总览"""
    use_tier_pricing: bool  # True=按量阶梯计费, False=按时计费
    hourly_price: float     # 按时计费价格（元/小时）
    tier_pricing: List[TierPricing]


class Monitor:
    """LLM-Manager 数据库监控器"""

    def __init__(self, db_path: Optional[str] = None):
        """初始化监控器

        Args:
            db_path: 数据库文件路径，默认为 webui/monitoring.db
        """
        if db_path is None:
            db_path = os.path.join("webui", "monitoring.db")

        self.db_path = db_path
        self.local = threading.local()
        self.config_manager = ConfigManager()

        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._initialize_database()

        logger.info(f"监控器初始化完成: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """获取线程本地连接（含PRAGMA配置）"""
        if not hasattr(self.local, 'conn') or self.local.conn is None:
            self.local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False
            )
            self.local.conn.row_factory = sqlite3.Row

            # 关键配置：外键约束 + WAL模式
            cursor = self.local.conn.cursor()
            cursor.execute('PRAGMA foreign_keys = ON;')
            cursor.execute('PRAGMA journal_mode = WAL;')
            cursor.close()

        return self.local.conn

    def _get_model_id(self, conn: sqlite3.Connection, model_name: str) -> Optional[int]:
        """获取模型ID，不存在则创建"""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM models WHERE original_name = ?",
            (model_name,)
        )
        result = cursor.fetchone()
        if result:
            return result['id']

        cursor.execute(
            "INSERT INTO models (original_name) VALUES (?)",
            (model_name,)
        )
        return cursor.lastrowid

    def _initialize_database(self):
        """初始化数据库表结构"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 模型表（核心元数据）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 程序运行时间表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS program_runtime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL
            )
        ''')

        # 模型运行时间表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_runtime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_model_runtime_model_id ON model_runtime(model_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_model_runtime_times ON model_runtime(start_time, end_time)')

        # 请求记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_n INTEGER NOT NULL,
                prompt_n INTEGER NOT NULL,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_model_requests_model_id ON model_requests(model_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_model_requests_times ON model_requests(end_time)')

        # 阶梯计费配置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tier_pricing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                tier_index INTEGER NOT NULL,
                min_input_tokens INTEGER NOT NULL,
                max_input_tokens INTEGER NOT NULL,
                min_output_tokens INTEGER NOT NULL,
                max_output_tokens INTEGER NOT NULL,
                input_price REAL NOT NULL,
                output_price REAL NOT NULL,
                support_cache BOOLEAN NOT NULL DEFAULT 0,
                cache_write_price REAL NOT NULL DEFAULT 0.0,
                cache_read_price REAL NOT NULL DEFAULT 0.0,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,
                UNIQUE(model_id, tier_index)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tier_pricing_model_id ON tier_pricing(model_id)')

        # 按时计费配置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hourly_pricing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL UNIQUE,
                hourly_price REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        ''')

        # 计费方式表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS billing_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL UNIQUE,
                use_tier_pricing BOOLEAN NOT NULL DEFAULT 1,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        ''')

        # 初始化现有模型
        model_names = self.config_manager.get_model_names()
        for model_name in model_names:
            self._get_model_id(conn, model_name)

            # 创建默认计费配置
            model_id = self._get_model_id(conn, model_name)

            cursor.execute('SELECT COUNT(*) FROM billing_methods WHERE model_id = ?', (model_id,))
            if cursor.fetchone()[0] == 0:
                cursor.execute('INSERT INTO billing_methods (model_id, use_tier_pricing) VALUES (?, 1)', (model_id,))

            cursor.execute('SELECT COUNT(*) FROM hourly_pricing WHERE model_id = ?', (model_id,))
            if cursor.fetchone()[0] == 0:
                cursor.execute('INSERT INTO hourly_pricing (model_id, hourly_price) VALUES (?, 0)', (model_id,))

            cursor.execute('SELECT COUNT(*) FROM tier_pricing WHERE model_id = ? AND tier_index = 1', (model_id,))
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO tier_pricing
                    (model_id, tier_index, min_input_tokens, max_input_tokens,
                     min_output_tokens, max_output_tokens, input_price, output_price,
                     support_cache, cache_write_price, cache_read_price)
                    VALUES (?, 1, 0, 32768, 0, 32768, 0.0, 0.0, 0, 0.0, 0.0)
                ''', (model_id,))

        conn.commit()
        logger.info(f"数据库初始化完成: {len(model_names)} 个模型")

    # ==================== 程序运行时间 ====================

    def add_program_runtime_start(self, start_time: float):
        """添加程序启动时间记录"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO program_runtime (start_time, end_time) VALUES (?, ?)",
                (start_time, start_time)
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"添加程序运行时间失败: {e}")
            raise

    def update_program_runtime_end(self, end_time: float):
        """更新程序运行时间记录的结束时间"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE program_runtime SET end_time = ? WHERE id = (SELECT MAX(id) FROM program_runtime)",
                (end_time,)
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"更新程序运行时间失败: {e}")
            raise

    def get_program_runtime(self, limit: int = 0) -> List[ModelRunTime]:
        """获取程序运行时间记录"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if limit > 0:
            cursor.execute('SELECT id, start_time, end_time FROM program_runtime ORDER BY id DESC LIMIT ?', (limit,))
        else:
            cursor.execute('SELECT id, start_time, end_time FROM program_runtime ORDER BY id DESC')

        return [ModelRunTime(row['id'], row['start_time'], row['end_time']) for row in cursor.fetchall()]

    # ==================== 模型运行时间 ====================

    def add_model_runtime_start(self, model_name: str, start_time: float):
        """添加模型启动时间记录"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            model_id = self._get_model_id(conn, model_name)
            if model_id is None:
                raise ValueError(f"模型不存在: {model_name}")

            cursor.execute(
                "INSERT INTO model_runtime (model_id, start_time, end_time) VALUES (?, ?, ?)",
                (model_id, start_time, start_time)
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"添加模型运行时间失败: model={model_name}, {e}")
            raise

    def update_model_runtime_end(self, model_name: str, end_time: float):
        """更新模型运行时间记录的结束时间"""
        conn = self._get_connection()
        try:
            model_id = self._get_model_id(conn, model_name)
            if model_id is None:
                return

            cursor = conn.cursor()
            cursor.execute('''
                UPDATE model_runtime SET end_time = ?
                WHERE id = (
                    SELECT rt.id FROM model_runtime rt
                    INNER JOIN models m ON rt.model_id = m.id
                    WHERE m.original_name = ? ORDER BY rt.id DESC LIMIT 1
                )
            ''', (end_time, model_name))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"更新模型运行时间失败: model={model_name}, {e}")
            raise

    def get_model_runtime_in_range(self, model_name: str, start_time: float, end_time: float) -> List[ModelRunTime]:
        """获取指定时间范围内的模型运行时间记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT rt.id, rt.start_time, rt.end_time
            FROM model_runtime rt
            INNER JOIN models m ON rt.model_id = m.id
            WHERE m.original_name = ? AND (rt.start_time <= ? AND (rt.end_time >= ? OR rt.end_time IS NULL))
            ORDER BY rt.start_time ASC
        ''', (model_name, end_time, start_time))

        return [ModelRunTime(row['id'], row['start_time'], row['end_time']) for row in cursor.fetchall()]

    # ==================== 请求记录 ====================

    def add_model_request(
        self,
        model_name: str,
        start_time: float,
        end_time: float,
        input_tokens: int,
        output_tokens: int,
        cache_n: int,
        prompt_n: int
    ):
        """添加模型请求记录（计费核心数据）"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            model_id = self._get_model_id(conn, model_name)
            if model_id is None:
                raise ValueError(f"模型不存在: {model_name}")

            cursor.execute('''
                INSERT INTO model_requests
                (model_id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (model_id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"添加请求记录失败: model={model_name}, {e}")
            raise

    def get_model_requests(
        self,
        model_name: str,
        start_time: float = 0,
        end_time: float = 0,
        buffer_seconds: int = 60
    ) -> List[ModelRequest]:
        """获取指定时间范围内的模型请求记录"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if end_time == 0:
            end_time = time.time()

        query_start_time = start_time - buffer_seconds if start_time > 0 else 0

        cursor.execute('''
            SELECT r.id, r.start_time, r.end_time, r.input_tokens, r.output_tokens, r.cache_n, r.prompt_n
            FROM model_requests r
            INNER JOIN models m ON r.model_id = m.id
            WHERE m.original_name = ? AND r.end_time >= ? AND r.end_time <= ?
            ORDER BY r.end_time ASC
        ''', (model_name, query_start_time, end_time))

        rows = cursor.fetchall()
        if start_time > 0:
            rows = [row for row in rows if row['end_time'] >= start_time]

        return [
            ModelRequest(
                id=row['id'], start_time=row['start_time'], end_time=row['end_time'],
                input_tokens=row['input_tokens'], output_tokens=row['output_tokens'],
                cache_n=row['cache_n'], prompt_n=row['prompt_n']
            )
            for row in rows
        ]

    # ==================== 计费配置 ====================

    def get_model_billing(self, model_name: str) -> Optional[ModelBilling]:
        """获取模型计费配置"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM models WHERE original_name = ?", (model_name,))
        model_result = cursor.fetchone()
        if not model_result:
            raise ValueError(f"模型不存在: {model_name}")

        model_id = model_result['id']

        # 获取计费方式
        cursor.execute("SELECT use_tier_pricing FROM billing_methods WHERE model_id = ?", (model_id,))
        billing_result = cursor.fetchone()
        if not billing_result:
            cursor.execute("INSERT INTO billing_methods (model_id, use_tier_pricing) VALUES (?, 1)", (model_id,))
            use_tier_pricing = True
        else:
            use_tier_pricing = bool(billing_result['use_tier_pricing'])

        # 获取按时价格
        cursor.execute("SELECT hourly_price FROM hourly_pricing WHERE model_id = ?", (model_id,))
        hourly_result = cursor.fetchone()
        if not hourly_result:
            cursor.execute("INSERT INTO hourly_pricing (model_id, hourly_price) VALUES (?, 0)", (model_id,))
            hourly_price = 0.0
        else:
            hourly_price = hourly_result['hourly_price']

        # 获取阶梯价格
        cursor.execute('''
            SELECT tier_index, min_input_tokens, max_input_tokens,
                   min_output_tokens, max_output_tokens, input_price, output_price,
                   support_cache, cache_write_price, cache_read_price
            FROM tier_pricing WHERE model_id = ? ORDER BY tier_index
        ''', (model_id,))

        tier_pricing = [
            TierPricing(
                tier_index=row['tier_index'],
                min_input_tokens=row['min_input_tokens'],
                max_input_tokens=row['max_input_tokens'],
                min_output_tokens=row['min_output_tokens'],
                max_output_tokens=row['max_output_tokens'],
                input_price=row['input_price'],
                output_price=row['output_price'],
                support_cache=bool(row['support_cache']),
                cache_write_price=row['cache_write_price'],
                cache_read_price=row['cache_read_price']
            )
            for row in cursor.fetchall()
        ]

        return ModelBilling(use_tier_pricing, hourly_price, tier_pricing)

    def upsert_tier_pricing(
        self,
        model_name: str,
        tier_index: int,
        min_input: int,
        max_input: int,
        min_output: int,
        max_output: int,
        input_price: float,
        output_price: float,
        support_cache: bool,
        cache_write_price: float,
        cache_read_price: float
    ):
        """新增或更新阶梯计费配置"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            model_id = self._get_model_id(conn, model_name)
            if model_id is None:
                raise ValueError(f"模型不存在: {model_name}")

            conn.execute('''
                INSERT INTO tier_pricing
                (model_id, tier_index, min_input_tokens, max_input_tokens,
                 min_output_tokens, max_output_tokens, input_price, output_price,
                 support_cache, cache_write_price, cache_read_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_id, tier_index) DO UPDATE SET
                    min_input_tokens = excluded.min_input_tokens,
                    max_input_tokens = excluded.max_input_tokens,
                    min_output_tokens = excluded.min_output_tokens,
                    max_output_tokens = excluded.max_output_tokens,
                    input_price = excluded.input_price,
                    output_price = excluded.output_price,
                    support_cache = excluded.support_cache,
                    cache_write_price = excluded.cache_write_price,
                    cache_read_price = excluded.cache_read_price
            ''', (model_id, tier_index, min_input, max_input, min_output, max_output,
                  input_price, output_price, 1 if support_cache else 0,
                  cache_write_price, cache_read_price))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"更新阶梯计费失败: model={model_name}, tier={tier_index}, {e}")
            raise

    def delete_and_reindex_tier(self, model_name: str, tier_index_to_delete: int):
        """删除阶梯并重新索引"""
        conn = self._get_connection()
        try:
            model_id = self._get_model_id(conn, model_name)
            if model_id is None:
                raise ValueError(f"模型不存在: {model_name}")

            conn.execute("DELETE FROM tier_pricing WHERE model_id = ? AND tier_index = ?", (model_id, tier_index_to_delete))

            remaining_tiers = list(conn.execute(
                "SELECT tier_index FROM tier_pricing WHERE model_id = ? ORDER BY tier_index ASC",
                (model_id,)
            ))

            for new_index, (old_index,) in enumerate(remaining_tiers, start=1):
                if new_index != old_index:
                    conn.execute("UPDATE tier_pricing SET tier_index = ? WHERE model_id = ? AND tier_index = ?",
                               (new_index, model_id, old_index))

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"删除阶梯失败: model={model_name}, tier={tier_index_to_delete}, {e}")
            raise

    def update_billing_method(self, model_name: str, use_tier_pricing: bool):
        """更新计费方式（True=按量阶梯, False=按时）"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            model_id = self._get_model_id(conn, model_name)
            if model_id is None:
                raise ValueError(f"模型不存在: {model_name}")

            conn.execute('''
                INSERT INTO billing_methods (model_id, use_tier_pricing) VALUES (?, ?)
                ON CONFLICT(model_id) DO UPDATE SET use_tier_pricing = excluded.use_tier_pricing
            ''', (model_id, 1 if use_tier_pricing else 0))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"更新计费方式失败: model={model_name}, use_tier={use_tier_pricing}, {e}")
            raise

    def update_hourly_price(self, model_name: str, hourly_price: float):
        """更新按时计费价格（元/小时）"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            model_id = self._get_model_id(conn, model_name)
            if model_id is None:
                raise ValueError(f"模型不存在: {model_name}")

            conn.execute('''
                INSERT INTO hourly_pricing (model_id, hourly_price) VALUES (?, ?)
                ON CONFLICT(model_id) DO UPDATE SET hourly_price = excluded.hourly_price
            ''', (model_id, hourly_price))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"更新按时价格失败: model={model_name}, price={hourly_price}, {e}")
            raise

    # ==================== 数据管理 ====================

    def get_all_db_models(self) -> List[str]:
        """获取数据库中所有模型名称"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT original_name FROM models ORDER BY original_name")
        return [row['original_name'] for row in cursor.fetchall()]

    def get_orphaned_models(self) -> List[str]:
        """获取孤立模型列表（存在于数据库但不在配置中）"""
        db_models = set(self.get_all_db_models())
        config_models = set(self.config_manager.get_model_names())
        return sorted(list(db_models - config_models))

    def delete_model_tables(self, model_name: str, auto_vacuum: bool = True):
        """删除模型的所有数据（级联删除相关表）
        
        Args:
            model_name: 模型名称
            auto_vacuum: 是否自动执行 VACUUM 回收空间 (默认 True)
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM models WHERE original_name = ?", (model_name,))
            conn.commit()
            logger.info(f"已删除模型数据：{model_name}")
            
            # 自动回收空间
            if auto_vacuum:
                try:
                    logger.info("执行 VACUUM 回收数据库空间...")
                    start_time = time.time()
                    conn.execute("VACUUM")
                    # 截断 WAL 文件
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    elapsed = time.time() - start_time
                    logger.info(f"VACUUM 完成，耗时：{elapsed:.2f} 秒")
                except Exception as v_e:
                    logger.warning(f"VACUUM 失败 (不影响删除结果): {v_e}")
                    
        except Exception as e:
            conn.rollback()
            logger.error(f"删除模型数据失败：model={model_name}, {e}")
            raise

    def get_single_model_storage_stats(self, model_name: str) -> Dict[str, Union[int, bool]]:
        """获取单个模型的存储统计信息

        Returns:
            {
                "request_count": int,      # 累计请求数
                "has_runtime_data": bool,  # 是否有运行时间记录
                "has_billing_data": bool   # 是否有计费配置
            }
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        stats = {
            "request_count": 0,
            "has_runtime_data": False,
            "has_billing_data": False
        }

        cursor.execute("SELECT id FROM models WHERE original_name = ?", (model_name,))
        model_result = cursor.fetchone()
        if not model_result:
            return stats

        model_id = model_result['id']

        cursor.execute("SELECT COUNT(*) FROM model_requests WHERE model_id = ?", (model_id,))
        stats["request_count"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM model_runtime WHERE model_id = ?", (model_id,))
        stats["has_runtime_data"] = cursor.fetchone()[0] > 0

        cursor.execute("SELECT COUNT(*) FROM tier_pricing WHERE model_id = ?", (model_id,))
        stats["has_billing_data"] = cursor.fetchone()[0] > 0

        return stats

    # ==================== 资源清理 ====================

    def close(self):
        """关闭监控器"""
        if hasattr(self.local, 'conn') and self.local.conn:
            try:
                self.local.conn.close()
                self.local.conn = None
            except Exception as e:
                logger.warning(f"关闭数据库连接时出错: {e}")
        logger.info("监控器已关闭")

    def __del__(self):
        """析构函数"""
        try:
            self.close()
        except:
            pass
