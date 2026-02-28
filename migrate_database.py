#!/usr/bin/env python3
"""
数据库迁移脚本

从旧的动态表结构迁移到新的统一表结构

迁移内容：
1. model_name_mapping -> models
2. {safe_name}_requests -> model_requests
3. {safe_name}_runtime -> model_runtime
4. {safe_name}_tier_pricing -> tier_pricing
5. {safe_name}_hourly_price -> hourly_pricing
6. {safe_name}_billing_method -> billing_methods
7. program_runtime -> program_runtime (保持不变)

使用方法：
    python migrate_database.py [--backup] [--dry-run]

选项：
    --backup     在迁移前自动备份旧数据库
    --dry-run    只显示迁移计划，不执行实际迁移
"""

import sqlite3
import shutil
import argparse
import os
import sys
from pathlib import Path
from utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseMigrator:
    """数据库迁移器"""

    def __init__(self, old_db_path: str, new_db_path: str):
        """
        初始化迁移器

        Args:
            old_db_path: 旧数据库路径
            new_db_path: 新数据库路径（如果为None，则覆盖旧数据库）
        """
        self.old_db_path = old_db_path
        self.new_db_path = new_db_path or old_db_path
        self.is_in_place = (new_db_path is None)

    def _get_safe_name(self, conn, model_name: str) -> str:
        """
        生成模型的安全名称（与旧代码逻辑一致）

        Args:
            conn: 数据库连接
            model_name: 原始模型名称

        Returns:
            安全名称（SHA256哈希前16位）
        """
        import hashlib
        hash_obj = hashlib.sha256(model_name.encode('utf-8'))
        return f"model_{hash_obj.hexdigest()[:16]}"

    def _get_old_model_names(self, conn) -> dict:
        """
        获取旧数据库中的模型映射

        Returns:
            {original_name: safe_name} 字典
        """
        cursor = conn.cursor()

        # 首先检查 model_name_mapping 表是否存在
        cursor.execute('''
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='model_name_mapping'
        ''')

        if cursor.fetchone():
            # 从映射表读取
            cursor.execute("SELECT original_name, safe_name FROM model_name_mapping")
            return {row[0]: row[1] for row in cursor.fetchall()}
        else:
            # 映射表不存在，说明是旧版本，需要通过推断
            # 获取所有表名，找出模型表
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            all_tables = [row[0] for row in cursor.fetchall()]

            # 查找所有 _requests 结尾的表，推断模型名称
            model_mapping = {}
            for table in all_tables:
                if table.endswith('_requests'):
                    # 提取 safe_name
                    safe_name = table.replace('_requests', '')
                    # 这里无法反推原始名称，只能使用 safe_name 作为原始名称
                    # 用户可能需要手动修正
                    model_mapping[safe_name] = safe_name
                    logger.warning(f"无法找到模型 '{safe_name}' 的原始名称，使用 safe_name 代替")

            return model_mapping

    def check_old_database(self) -> bool:
        """
        检查旧数据库是否存在且有效

        Returns:
            数据库是否有效
        """
        if not os.path.exists(self.old_db_path):
            logger.error(f"旧数据库文件不存在: {self.old_db_path}")
            return False

        try:
            conn = sqlite3.connect(self.old_db_path)
            cursor = conn.cursor()

            # 检查是否有表
            cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
            table_count = cursor.fetchone()[0]

            if table_count == 0:
                logger.error("旧数据库没有任何表")
                return False

            conn.close()
            return True

        except Exception as e:
            logger.error(f"检查旧数据库时出错: {e}")
            return False

    def create_new_database(self):
        """创建新数据库结构"""
        logger.info("创建新数据库结构...")

        conn = sqlite3.connect(self.new_db_path)
        cursor = conn.cursor()

        # 1. 创建模型表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 2. 创建程序运行时间表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS program_runtime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL
            )
        ''')

        # 3. 创建模型运行时间表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_runtime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_model_runtime_model_id
            ON model_runtime(model_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_model_runtime_times
            ON model_runtime(start_time, end_time)
        ''')

        # 4. 创建请求记录表
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

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_model_requests_model_id
            ON model_requests(model_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_model_requests_times
            ON model_requests(end_time)
        ''')

        # 5. 创建阶梯计费配置表
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

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_tier_pricing_model_id
            ON tier_pricing(model_id)
        ''')

        # 6. 创建按时计费配置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hourly_pricing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL UNIQUE,
                hourly_price REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        ''')

        # 7. 创建计费方式表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS billing_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL UNIQUE,
                use_tier_pricing BOOLEAN NOT NULL DEFAULT 1,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        ''')

        conn.commit()
        conn.close()

        logger.info("新数据库结构创建完成")

    def migrate_program_runtime(self, old_conn, new_conn):
        """
        迁移程序运行时间记录

        这个表在旧新版本中结构相同，直接复制
        """
        logger.info("迁移程序运行时间记录...")

        old_cursor = old_conn.cursor()
        new_cursor = new_conn.cursor()

        # 检查旧表是否存在
        old_cursor.execute('''
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='program_runtime'
        ''')

        if not old_cursor.fetchone():
            logger.warning("旧数据库中没有 program_runtime 表，跳过")
            return

        # 复制数据
        old_cursor.execute("SELECT id, start_time, end_time FROM program_runtime")
        rows = old_cursor.fetchall()

        for row in rows:
            try:
                new_cursor.execute(
                    "INSERT INTO program_runtime (id, start_time, end_time) VALUES (?, ?, ?)",
                    row
                )
            except sqlite3.IntegrityError:
                # ID冲突，跳过
                pass

        new_conn.commit()
        logger.info(f"已迁移 {len(rows)} 条程序运行时间记录")

    def migrate_model_mapping(self, old_conn, new_conn) -> dict:
        """
        迁移模型映射表

        Returns:
            {original_name: model_id} 字典
        """
        logger.info("迁移模型映射...")

        old_cursor = old_conn.cursor()
        new_cursor = new_conn.cursor()

        model_mapping = self._get_old_model_names(old_conn)
        model_id_map = {}

        for original_name, safe_name in model_mapping.items():
            # 插入到新表
            try:
                new_cursor.execute(
                    "INSERT INTO models (original_name) VALUES (?)",
                    (original_name,)
                )
                model_id = new_cursor.lastrowid
                model_id_map[original_name] = model_id
            except sqlite3.IntegrityError:
                # 已存在，获取ID
                new_cursor.execute(
                    "SELECT id FROM models WHERE original_name = ?",
                    (original_name,)
                )
                model_id = new_cursor.fetchone()[0]
                model_id_map[original_name] = model_id

        new_conn.commit()
        logger.info(f"已迁移 {len(model_id_map)} 个模型映射")

        return model_id_map

    def migrate_model_requests(self, old_conn, new_conn, model_mapping: dict, model_id_map: dict):
        """
        迁移模型请求记录

        Args:
            model_mapping: {original_name: safe_name}
            model_id_map: {original_name: model_id}
        """
        logger.info("迁移模型请求记录...")

        old_cursor = old_conn.cursor()
        new_cursor = new_conn.cursor()

        total_migrated = 0

        for original_name, safe_name in model_mapping.items():
            model_id = model_id_map.get(original_name)
            if model_id is None:
                logger.warning(f"模型 '{original_name}' 不在 model_id_map 中，跳过")
                continue

            old_table = f"{safe_name}_requests"

            # 检查旧表是否存在
            old_cursor.execute(f'''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{old_table}'
            ''')

            if not old_cursor.fetchone():
                logger.warning(f"表 {old_table} 不存在，跳过")
                continue

            # 检查表结构，判断是否有 start_time 字段
            old_cursor.execute(f"PRAGMA table_info({old_table})")
            columns_info = old_cursor.fetchall()
            columns = [col[1] for col in columns_info]

            # 读取数据
            if 'start_time' in columns:
                # 新版本结构
                old_cursor.execute(f'''
                    SELECT id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n
                    FROM {old_table}
                ''')
            else:
                # 旧版本结构（只有 timestamp）
                old_cursor.execute(f'''
                    SELECT id, 0 as start_time, timestamp as end_time,
                           input_tokens, output_tokens, cache_n, prompt_n
                    FROM {old_table}
                ''')

            rows = old_cursor.fetchall()

            # 插入新表
            for row in rows:
                try:
                    new_cursor.execute('''
                        INSERT INTO model_requests
                        (model_id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (model_id, row[1], row[2], row[3], row[4], row[5], row[6]))
                    total_migrated += 1
                except Exception as e:
                    logger.warning(f"插入请求记录失败: {e}")

        new_conn.commit()
        logger.info(f"已迁移 {total_migrated} 条请求记录")

    def migrate_model_runtime(self, old_conn, new_conn, model_mapping: dict, model_id_map: dict):
        """
        迁移模型运行时间记录

        Args:
            model_mapping: {original_name: safe_name}
            model_id_map: {original_name: model_id}
        """
        logger.info("迁移模型运行时间记录...")

        old_cursor = old_conn.cursor()
        new_cursor = new_conn.cursor()

        total_migrated = 0

        for original_name, safe_name in model_mapping.items():
            model_id = model_id_map.get(original_name)
            if model_id is None:
                continue

            old_table = f"{safe_name}_runtime"

            # 检查旧表是否存在
            old_cursor.execute(f'''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{old_table}'
            ''')

            if not old_cursor.fetchone():
                continue

            # 读取数据
            old_cursor.execute(f'''
                SELECT id, start_time, end_time
                FROM {old_table}
            ''')

            rows = old_cursor.fetchall()

            # 插入新表
            for row in rows:
                try:
                    new_cursor.execute('''
                        INSERT INTO model_runtime
                        (model_id, start_time, end_time)
                        VALUES (?, ?, ?)
                    ''', (model_id, row[1], row[2]))
                    total_migrated += 1
                except Exception as e:
                    logger.warning(f"插入运行时间记录失败: {e}")

        new_conn.commit()
        logger.info(f"已迁移 {total_migrated} 条运行时间记录")

    def migrate_tier_pricing(self, old_conn, new_conn, model_mapping: dict, model_id_map: dict):
        """
        迁移阶梯计费配置

        Args:
            model_mapping: {original_name: safe_name}
            model_id_map: {original_name: model_id}
        """
        logger.info("迁移阶梯计费配置...")

        old_cursor = old_conn.cursor()
        new_cursor = new_conn.cursor()

        total_migrated = 0

        for original_name, safe_name in model_mapping.items():
            model_id = model_id_map.get(original_name)
            if model_id is None:
                continue

            old_table = f"{safe_name}_tier_pricing"

            # 检查旧表是否存在
            old_cursor.execute(f'''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{old_table}'
            ''')

            if not old_cursor.fetchone():
                continue

            # 读取数据
            old_cursor.execute(f'''
                SELECT tier_index, min_input_tokens, max_input_tokens,
                       min_output_tokens, max_output_tokens,
                       input_price, output_price, support_cache,
                       cache_write_price, cache_read_price
                FROM {old_table}
            ''')

            rows = old_cursor.fetchall()

            # 插入新表
            for row in rows:
                try:
                    new_cursor.execute('''
                        INSERT INTO tier_pricing
                        (model_id, tier_index, min_input_tokens, max_input_tokens,
                         min_output_tokens, max_output_tokens, input_price, output_price,
                         support_cache, cache_write_price, cache_read_price)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (model_id,) + row)
                    total_migrated += 1
                except Exception as e:
                    logger.warning(f"插入阶梯计费配置失败: {e}")

        new_conn.commit()
        logger.info(f"已迁移 {total_migrated} 条阶梯计费配置")

    def migrate_hourly_pricing(self, old_conn, new_conn, model_mapping: dict, model_id_map: dict):
        """
        迁移按时计费配置

        Args:
            model_mapping: {original_name: safe_name}
            model_id_map: {original_name: model_id}
        """
        logger.info("迁移按时计费配置...")

        old_cursor = old_conn.cursor()
        new_cursor = new_conn.cursor()

        total_migrated = 0

        for original_name, safe_name in model_mapping.items():
            model_id = model_id_map.get(original_name)
            if model_id is None:
                continue

            old_table = f"{safe_name}_hourly_price"

            # 检查旧表是否存在
            old_cursor.execute(f'''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{old_table}'
            ''')

            if not old_cursor.fetchone():
                continue

            # 读取数据
            old_cursor.execute(f'''
                SELECT hourly_price FROM {old_table} WHERE id = 1
            ''')

            row = old_cursor.fetchone()
            if row:
                try:
                    new_cursor.execute('''
                        INSERT INTO hourly_pricing (model_id, hourly_price)
                        VALUES (?, ?)
                    ''', (model_id, row[0]))
                    total_migrated += 1
                except Exception as e:
                    logger.warning(f"插入按时计费配置失败: {e}")

        new_conn.commit()
        logger.info(f"已迁移 {total_migrated} 条按时计费配置")

    def migrate_billing_methods(self, old_conn, new_conn, model_mapping: dict, model_id_map: dict):
        """
        迁移计费方式配置

        Args:
            model_mapping: {original_name: safe_name}
            model_id_map: {original_name: model_id}
        """
        logger.info("迁移计费方式配置...")

        old_cursor = old_conn.cursor()
        new_cursor = new_conn.cursor()

        total_migrated = 0

        for original_name, safe_name in model_mapping.items():
            model_id = model_id_map.get(original_name)
            if model_id is None:
                continue

            old_table = f"{safe_name}_billing_method"

            # 检查旧表是否存在
            old_cursor.execute(f'''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{old_table}'
            ''')

            if not old_cursor.fetchone():
                continue

            # 读取数据
            old_cursor.execute(f'''
                SELECT use_tier_pricing FROM {old_table} WHERE id = 1
            ''')

            row = old_cursor.fetchone()
            if row:
                try:
                    new_cursor.execute('''
                        INSERT INTO billing_methods (model_id, use_tier_pricing)
                        VALUES (?, ?)
                    ''', (model_id, row[0]))
                    total_migrated += 1
                except Exception as e:
                    logger.warning(f"插入计费方式配置失败: {e}")

        new_conn.commit()
        logger.info(f"已迁移 {total_migrated} 条计费方式配置")

    def migrate(self, dry_run: bool = False):
        """
        执行完整的迁移流程

        Args:
            dry_run: 是否为演习模式（只显示计划，不执行）
        """
        logger.info("=" * 60)
        logger.info("开始数据库迁移")
        logger.info(f"旧数据库: {self.old_db_path}")
        logger.info(f"新数据库: {self.new_db_path}")
        logger.info(f"迁移模式: {'演习模式（不执行）' if dry_run else '实际迁移'}")
        logger.info("=" * 60)

        # 检查旧数据库
        if not self.check_old_database():
            logger.error("旧数据库无效，迁移终止")
            return False

        # 连接旧数据库
        old_conn = sqlite3.connect(self.old_db_path)

        # 获取模型映射（用于显示信息）
        model_mapping = self._get_old_model_names(old_conn)
        logger.info(f"发现 {len(model_mapping)} 个模型需要迁移")

        if dry_run:
            logger.info("\n演习模式：迁移计划预览")
            logger.info(f"- 模型数量: {len(model_mapping)}")
            for model_name in model_mapping.keys():
                logger.info(f"  - {model_name}")
            logger.info("\n演习模式结束，未执行实际迁移")
            old_conn.close()
            return True

        # 如果是原位迁移，先创建临时文件
        if self.is_in_place:
            temp_db = self.old_db_path + ".new"
            if os.path.exists(temp_db):
                os.remove(temp_db)
            self.new_db_path = temp_db

        # 创建新数据库
        self.create_new_database()

        # 连接新数据库
        new_conn = sqlite3.connect(self.new_db_path)

        try:
            # 1. 迁移程序运行时间
            self.migrate_program_runtime(old_conn, new_conn)

            # 2. 迁移模型映射
            model_id_map = self.migrate_model_mapping(old_conn, new_conn)

            # 3. 迁移模型请求记录
            self.migrate_model_requests(old_conn, new_conn, model_mapping, model_id_map)

            # 4. 迁移模型运行时间
            self.migrate_model_runtime(old_conn, new_conn, model_mapping, model_id_map)

            # 5. 迁移阶梯计费配置
            self.migrate_tier_pricing(old_conn, new_conn, model_mapping, model_id_map)

            # 6. 迁移按时计费配置
            self.migrate_hourly_pricing(old_conn, new_conn, model_mapping, model_id_map)

            # 7. 迁移计费方式配置
            self.migrate_billing_methods(old_conn, new_conn, model_mapping, model_id_map)

            # 关闭连接
            old_conn.close()
            new_conn.close()

            # 如果是原位迁移，替换旧数据库
            if self.is_in_place:
                logger.info("\n替换旧数据库...")

                # 备份旧数据库
                backup_path = self.old_db_path + ".backup"
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                shutil.move(self.old_db_path, backup_path)
                logger.info(f"旧数据库已备份到: {backup_path}")

                # 使用新数据库
                shutil.move(temp_db, self.old_db_path)
                logger.info(f"新数据库已安装到: {self.old_db_path}")

            logger.info("\n" + "=" * 60)
            logger.info("数据库迁移完成！")
            logger.info("=" * 60)
            return True

        except Exception as e:
            logger.error(f"迁移过程中出错: {e}")
            logger.error("迁移已中止，数据库未修改")

            # 清理临时文件
            if self.is_in_place and os.path.exists(temp_db):
                os.remove(temp_db)
                logger.info("已清理临时文件")

            old_conn.close()
            new_conn.close()
            return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="数据库迁移脚本 - 从旧结构迁移到新结构",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 预览迁移计划（不执行）
  python migrate_database.py --dry-run

  # 执行迁移（自动备份旧数据库）
  python migrate_database.py --backup

  # 迁移到新文件
  python migrate_database.py --output webui/monitoring_new.db
        """
    )

    parser.add_argument(
        '--input',
        default='webui/monitoring.db',
        help='旧数据库路径（默认: webui/monitoring.db）'
    )

    parser.add_argument(
        '--output',
        default=None,
        help='新数据库路径（默认: 覆盖旧数据库）'
    )

    parser.add_argument(
        '--backup',
        action='store_true',
        help='在迁移前自动备份旧数据库'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='演习模式：只显示迁移计划，不执行实际迁移'
    )

    args = parser.parse_args()

    # 检查旧数据库
    if not os.path.exists(args.input):
        logger.error(f"数据库文件不存在: {args.input}")
        sys.exit(1)

    # 创建备份
    if args.backup and not args.dry_run:
        backup_path = args.input + f".backup.{int(time.time())}"
        logger.info(f"创建备份: {backup_path}")
        shutil.copy2(args.input, backup_path)
        logger.info("备份完成")

    # 创建迁移器
    migrator = DatabaseMigrator(args.input, args.output)

    # 执行迁移
    success = migrator.migrate(dry_run=args.dry_run)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    import time
    main()
