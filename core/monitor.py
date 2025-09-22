#!/usr/bin/env python3
"""
LLM-Manager 监控器
提供线程安全的数据库操作，用于监控模型运行状态、请求记录和计费管理
"""

import sqlite3
import threading
import hashlib
import time
import os
from typing import Dict, List, Optional, Union, Any
from dataclasses import dataclass
from contextlib import contextmanager
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
    """模型请求记录"""
    id: int
    timestamp: float
    input_tokens: int
    output_tokens: int

@dataclass
class TierPricing:
    """阶梯计费配置"""
    tier_index: int
    start_tokens: int
    end_tokens: int
    input_price_per_million: float
    output_price_per_million: float

@dataclass
class ModelBilling:
    """模型计费配置"""
    use_tier_pricing: bool
    hourly_price: float
    tier_pricing: List[TierPricing]

class DatabaseConnectionPool:
    """线程安全的数据库连接池"""

    def __init__(self, db_path: str, max_connections: int = 10):
        self.db_path = db_path
        self.max_connections = max_connections
        self.connections: List[sqlite3.Connection] = []
        self.available_connections: List[sqlite3.Connection] = []
        self.lock = threading.Lock()

    def get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        with self.lock:
            if self.available_connections:
                return self.available_connections.pop()

            if len(self.connections) < self.max_connections:
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                self.connections.append(conn)
                return conn

            raise RuntimeError("数据库连接池已满")

    def return_connection(self, conn: sqlite3.Connection):
        """归还数据库连接"""
        with self.lock:
            if conn in self.connections and conn not in self.available_connections:
                self.available_connections.append(conn)

    def close_all(self):
        """关闭所有连接"""
        with self.lock:
            for conn in self.connections:
                try:
                    conn.close()
                except:
                    pass
            self.connections.clear()
            self.available_connections.clear()

@contextmanager
def get_db_connection(pool: DatabaseConnectionPool):
    """获取数据库连接的上下文管理器"""
    conn = pool.get_connection()
    try:
        yield conn
    finally:
        pool.return_connection(conn)

class Monitor:
    """LLM-Manager 监控器"""

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化监控器

        Args:
            db_path: 数据库文件路径，默认为webui/monitoring.db
        """
        if db_path is None:
            db_path = os.path.join("webui", "monitoring.db")

        self.db_path = db_path
        self.connection_pool = DatabaseConnectionPool(db_path)
        self.config_manager = ConfigManager()

        # 确保webui目录存在
        if os.path.dirname(db_path):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # 初始化数据库
        self._initialize_database()

        logger.info(f"监控器初始化完成，数据库路径: {db_path}")

    def get_safe_model_name(self, model_name: str) -> str:
        """
        获取安全化模型名称

        Args:
            model_name: 原始模型名称

        Returns:
            安全化的模型名称（SHA256哈希值）
        """
        # 使用SHA256哈希确保唯一性和安全性
        hash_obj = hashlib.sha256(model_name.encode('utf-8'))
        return f"model_{hash_obj.hexdigest()[:16]}"  # 取前16位作为表名前缀

    def _initialize_database(self):
        """初始化数据库和必要的表"""
        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()

            # 创建模型名称映射表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS model_name_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_name TEXT UNIQUE NOT NULL,
                    safe_name TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 创建程序运行时间表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS program_runtime (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time REAL NOT NULL,
                    end_time REAL NOT NULL
                )
            ''')

            # 获取所有模型并创建对应的表
            model_names = self.config_manager.get_model_names()
            for model_name in model_names:
                safe_name = self.get_safe_model_name(model_name)

                # 在映射表中记录对应关系 - 先检查是否存在
                cursor.execute('''
                    SELECT COUNT(*) FROM model_name_mapping WHERE original_name = ?
                ''', (model_name,))
                if cursor.fetchone()[0] == 0:
                    cursor.execute('''
                        INSERT INTO model_name_mapping (original_name, safe_name)
                        VALUES (?, ?)
                    ''', (model_name, safe_name))

                # 创建模型运行时间表
                cursor.execute(f'''
                    CREATE TABLE IF NOT EXISTS {safe_name}_runtime (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        start_time REAL NOT NULL,
                        end_time REAL NOT NULL
                    )
                ''')

                # 创建模型请求记录表
                cursor.execute(f'''
                    CREATE TABLE IF NOT EXISTS {safe_name}_requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        input_tokens INTEGER NOT NULL,
                        output_tokens INTEGER NOT NULL
                    )
                ''')

                # 创建模型按量分阶计费表
                cursor.execute(f'''
                    CREATE TABLE IF NOT EXISTS {safe_name}_tier_pricing (
                        tier_index INTEGER PRIMARY KEY,
                        start_tokens INTEGER NOT NULL,
                        end_tokens INTEGER NOT NULL,
                        input_price_per_million REAL NOT NULL,
                        output_price_per_million REAL NOT NULL
                    )
                ''')

                # 检查是否有默认数据，没有则插入
                cursor.execute(f'''
                    SELECT COUNT(*) FROM {safe_name}_tier_pricing WHERE tier_index = 1
                ''')
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f'''
                        INSERT INTO {safe_name}_tier_pricing
                        (tier_index, start_tokens, end_tokens, input_price_per_million, output_price_per_million)
                        VALUES (1, 0, 32768, 0, 0)
                    ''')

                # 创建模型按时计费价格表
                cursor.execute(f'''
                    CREATE TABLE IF NOT EXISTS {safe_name}_hourly_price (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        hourly_price REAL NOT NULL DEFAULT 0
                    )
                ''')

                # 检查是否有默认数据，没有则插入
                cursor.execute(f'''
                    SELECT COUNT(*) FROM {safe_name}_hourly_price
                ''')
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f'''
                        INSERT INTO {safe_name}_hourly_price (id, hourly_price)
                        VALUES (1, 0)
                    ''')

                # 创建模型计费方式选择表
                cursor.execute(f'''
                    CREATE TABLE IF NOT EXISTS {safe_name}_billing_method (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        use_tier_pricing BOOLEAN NOT NULL DEFAULT 1
                    )
                ''')

                # 检查是否有默认数据，没有则插入
                cursor.execute(f'''
                    SELECT COUNT(*) FROM {safe_name}_billing_method
                ''')
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f'''
                        INSERT INTO {safe_name}_billing_method (id, use_tier_pricing)
                        VALUES (1, 1)
                    ''')

            conn.commit()
            logger.info(f"数据库初始化完成，处理了 {len(model_names)} 个模型")

    def get_model_safe_name(self, model_name: str) -> Optional[str]:
        """
        根据原始模型名称获取安全化名称

        Args:
            model_name: 原始模型名称

        Returns:
            安全化名称，如果不存在则返回None
        """
        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT safe_name FROM model_name_mapping WHERE original_name = ?
            ''', (model_name,))
            result = cursor.fetchone()
            return result[0] if result else None

    def add_model_runtime_start(self, model_name: str, start_time: float):
        """
        添加模型启动时间记录

        Args:
            model_name: 模型名称
            start_time: 启动时间戳
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                INSERT INTO {safe_name}_runtime (start_time, end_time)
                VALUES (?, ?)
            ''', (start_time, start_time))
            conn.commit()

    def update_model_runtime_end(self, model_name: str, end_time: float):
        """
        更新模型运行时间记录的结束时间

        Args:
            model_name: 模型名称
            end_time: 结束时间戳
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE {safe_name}_runtime
                SET end_time = ?
                WHERE id = (SELECT MAX(id) FROM {safe_name}_runtime)
            ''', (end_time,))
            conn.commit()

    def add_model_request(self, model_name: str, request_data: List[Union[float, int, int]]):
        """
        添加模型请求记录

        Args:
            model_name: 模型名称
            request_data: [时间戳, 输入token数, 输出token数]
        """
        if len(request_data) != 3:
            raise ValueError("请求数据格式错误，应为 [时间戳, 输入token数, 输出token数]")

        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        timestamp, input_tokens, output_tokens = request_data
        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                INSERT INTO {safe_name}_requests (timestamp, input_tokens, output_tokens)
                VALUES (?, ?, ?)
            ''', (timestamp, input_tokens, output_tokens))
            conn.commit()

    def add_tier_pricing(self, model_name: str, tier_data: List[Union[int, int, int, float, float]]):
        """
        添加计费阶梯

        Args:
            model_name: 模型名称
            tier_data: [阶梯索引, 起始token数, 结束token数, 输入价格/百万token, 输出价格/百万token]
        """
        if len(tier_data) != 5:
            raise ValueError("阶梯数据格式错误")

        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        tier_index, start_tokens, end_tokens, input_price, output_price = tier_data
        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                INSERT INTO {safe_name}_tier_pricing
                (tier_index, start_tokens, end_tokens, input_price_per_million, output_price_per_million)
                VALUES (?, ?, ?, ?, ?)
            ''', (tier_index, start_tokens, end_tokens, input_price, output_price))
            conn.commit()

    def update_tier_pricing(self, model_name: str, tier_data: List[Union[int, int, int, float, float]]):
        """
        更新计费阶梯

        Args:
            model_name: 模型名称
            tier_data: [阶梯索引, 起始token数, 结束token数, 输入价格/百万token, 输出价格/百万token]
        """
        if len(tier_data) != 5:
            raise ValueError("阶梯数据格式错误")

        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        tier_index, start_tokens, end_tokens, input_price, output_price = tier_data
        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE {safe_name}_tier_pricing
                SET start_tokens = ?, end_tokens = ?,
                    input_price_per_million = ?, output_price_per_million = ?
                WHERE tier_index = ?
            ''', (start_tokens, end_tokens, input_price, output_price, tier_index))
            conn.commit()

    def delete_tier_pricing(self, model_name: str, tier_index: int):
        """
        删除计费阶梯

        Args:
            model_name: 模型名称
            tier_index: 阶梯索引
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                DELETE FROM {safe_name}_tier_pricing WHERE tier_index = ?
            ''', (tier_index,))
            conn.commit()

    def update_hourly_price(self, model_name: str, hourly_price: float):
        """
        更新按时计费价格

        Args:
            model_name: 模型名称
            hourly_price: 每小时价格
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE {safe_name}_hourly_price SET hourly_price = ?
            ''', (hourly_price,))
            conn.commit()

    def update_billing_method(self, model_name: str, use_tier_pricing: bool):
        """
        更新计费方式

        Args:
            model_name: 模型名称
            use_tier_pricing: 是否使用按量计费
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE {safe_name}_billing_method SET use_tier_pricing = ?
            ''', (1 if use_tier_pricing else 0,))
            conn.commit()

    def add_program_runtime_start(self, start_time: float):
        """
        添加程序启动时间记录

        Args:
            start_time: 启动时间戳
        """
        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO program_runtime (start_time, end_time)
                VALUES (?, ?)
            ''', (start_time, start_time))
            conn.commit()

    def update_program_runtime_end(self, end_time: float):
        """
        更新程序运行时间记录的结束时间

        Args:
            end_time: 结束时间戳
        """
        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE program_runtime
                SET end_time = ?
                WHERE id = (SELECT MAX(id) FROM program_runtime)
            ''', (end_time,))
            conn.commit()

    def get_program_runtime(self, limit: int = 0) -> List[ModelRunTime]:
        """
        获取程序运行时间记录

        Args:
            limit: 限制返回的记录数，0表示返回所有记录

        Returns:
            程序运行时间记录列表
        """
        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            if limit > 0:
                cursor.execute('''
                    SELECT id, start_time, end_time FROM program_runtime
                    ORDER BY id DESC LIMIT ?
                ''', (limit,))
            else:
                cursor.execute('''
                    SELECT id, start_time, end_time FROM program_runtime
                    ORDER BY id DESC
                ''')

            return [ModelRunTime(row[0], row[1], row[2]) for row in cursor.fetchall()]

    def get_model_runtime(self, model_name: str, limit: int = 0) -> List[ModelRunTime]:
        """
        获取模型运行时间记录

        Args:
            model_name: 模型名称
            limit: 限制返回的记录数，0表示返回所有记录

        Returns:
            模型运行时间记录列表
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            if limit > 0:
                cursor.execute(f'''
                    SELECT id, start_time, end_time FROM {safe_name}_runtime
                    ORDER BY id DESC LIMIT ?
                ''', (limit,))
            else:
                cursor.execute(f'''
                    SELECT id, start_time, end_time FROM {safe_name}_runtime
                    ORDER BY id DESC
                ''')

            return [ModelRunTime(row[0], row[1], row[2]) for row in cursor.fetchall()]

    def get_model_requests(self, model_name: str, minutes: int = 0) -> List[ModelRequest]:
        """
        获取模型请求记录

        Args:
            model_name: 模型名称
            minutes: 限制返回的分钟数，0表示返回所有记录

        Returns:
            模型请求记录列表
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()
            if minutes > 0:
                cutoff_time = time.time() - (minutes * 60)
                cursor.execute(f'''
                    SELECT id, timestamp, input_tokens, output_tokens
                    FROM {safe_name}_requests
                    WHERE timestamp >= ?
                    ORDER BY id DESC
                ''', (cutoff_time,))
            else:
                cursor.execute(f'''
                    SELECT id, timestamp, input_tokens, output_tokens
                    FROM {safe_name}_requests
                    ORDER BY id DESC
                ''')

            return [ModelRequest(row[0], row[1], row[2], row[3]) for row in cursor.fetchall()]

    def get_model_billing(self, model_name: str) -> Optional[ModelBilling]:
        """
        获取模型计费配置

        Args:
            model_name: 模型名称

        Returns:
            模型计费配置
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()

            # 获取计费方式
            cursor.execute(f'''
                SELECT use_tier_pricing FROM {safe_name}_billing_method WHERE id = 1
            ''')
            billing_result = cursor.fetchone()
            if not billing_result:
                return None

            use_tier_pricing = bool(billing_result[0])

            # 获取按时价格
            cursor.execute(f'''
                SELECT hourly_price FROM {safe_name}_hourly_price WHERE id = 1
            ''')
            hourly_price = cursor.fetchone()[0]

            # 获取阶梯价格
            cursor.execute(f'''
                SELECT tier_index, start_tokens, end_tokens,
                       input_price_per_million, output_price_per_million
                FROM {safe_name}_tier_pricing
                ORDER BY tier_index
            ''')

            tier_pricing = [
                TierPricing(row[0], row[1], row[2], row[3], row[4])
                for row in cursor.fetchall()
            ]

            return ModelBilling(use_tier_pricing, hourly_price, tier_pricing)

    def delete_model_tables(self, model_name: str):
        """
        删除模型相关的所有表和记录

        Args:
            model_name: 模型名称
        """
        safe_name = self.get_model_safe_name(model_name)
        if not safe_name:
            raise ValueError(f"模型 '{model_name}' 不存在")

        with get_db_connection(self.connection_pool) as conn:
            cursor = conn.cursor()

            # 删除各个表
            tables_to_drop = [
                f"{safe_name}_runtime",
                f"{safe_name}_requests",
                f"{safe_name}_tier_pricing",
                f"{safe_name}_hourly_price",
                f"{safe_name}_billing_method"
            ]

            for table in tables_to_drop:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")

            # 删除映射记录
            cursor.execute('''
                DELETE FROM model_name_mapping WHERE original_name = ?
            ''', (model_name,))

            conn.commit()
            logger.info(f"已删除模型 '{model_name}' 的所有监控数据")

    def close(self):
        """关闭监控器，清理资源"""
        self.connection_pool.close_all()
        logger.info("监控器已关闭")

    def __del__(self):
        """析构函数"""
        try:
            self.close()
        except:
            pass