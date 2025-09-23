#!/usr/bin/env python3
"""
日志流测试脚本 - 用于测试模型日志流式接口
持续尝试连接指定模型的日志流，成功后输出历史日志并实时推送
"""

import requests
import json
import time
import threading
import sys
import os
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import get_logger
from core.config_manager import ConfigManager

logger = get_logger(__name__)


class LogStreamTester:
    """日志流测试器"""

    def __init__(self, model_alias: str = "Qwen3-8B-AWQ", base_url: str = "http://localhost:8000"):
        """
        初始化日志流测试器

        Args:
            model_alias: 模型别名
            base_url: API服务器基础URL
        """
        self.model_alias = model_alias
        self.base_url = base_url
        self.running = False
        self.session = requests.Session()

        # 读取配置文件获取实际端口
        try:
            config_manager = ConfigManager()
            api_config = config_manager.get_openai_config()
            host = api_config['host']
            port = api_config['port']

            # 在Windows上，0.0.0.0需要改为localhost才能访问
            if host == '0.0.0.0':
                host = 'localhost'

            self.base_url = f"http://{host}:{port}"
            logger.info(f"从配置文件读取API地址: {self.base_url}")
        except Exception as e:
            logger.warning(f"无法读取配置文件，使用默认地址: {e}")

    def check_model_status(self) -> bool:
        """
        检查模型状态

        Returns:
            模型是否已启动
        """
        try:
            response = self.session.get(f"{self.base_url}/v1/models", timeout=5)
            if response.status_code == 200:
                models_data = response.json()
                for model in models_data.get("data", []):
                    if self.model_alias in model.get("aliases", [self.model_alias]):
                        logger.info(f"在模型列表中找到 {self.model_alias}")
                        return True
            return False
        except Exception as e:
            logger.debug(f"检查模型状态失败: {e}")
            return False

    def test_stream_connection(self) -> bool:
        """
        测试流式连接

        Returns:
            是否成功
        """
        url = f"{self.base_url}/api/models/{self.model_alias}/logs/stream"

        try:
            with self.session.get(url, stream=True, timeout=10) as response:
                if response.status_code == 200:
                    logger.info("✅ 流式连接测试成功")
                    return True
                else:
                    logger.error(f"❌ 流式连接失败: HTTP {response.status_code}")
                    return False

        except Exception as e:
            logger.error(f"❌ 流式连接异常: {e}")
            return False

    def stream_logs(self):
        """流式获取日志"""
        url = f"{self.base_url}/api/models/{self.model_alias}/logs/stream"

        logger.info(f"开始连接日志流: {url}")

        try:
            with self.session.get(url, stream=True, timeout=3600) as response:
                if response.status_code != 200:
                    logger.error(f"连接日志流失败，状态码: {response.status_code}")
                    if response.status_code == 400:
                        logger.error("模型未启动或已停止，继续等待...")
                    return False

                logger.info("✅ 成功连接到日志流！")
                logger.info("=" * 60)

                # 处理流式数据
                for line in response.iter_lines():
                    if line:
                        line = line.decode('utf-8')

                        # 跳过心跳行
                        if line.strip() == '':
                            continue

                        # 解析SSE数据
                        if line.startswith('data: '):
                            data_str = line[6:]  # 去掉 'data: ' 前缀

                            try:
                                data = json.loads(data_str)
                                self._process_log_entry(data)
                            except json.JSONDecodeError as e:
                                logger.warning(f"解析JSON失败: {e}, 原始数据: {data_str}")

                        # 检查是否应该停止
                        if not self.running:
                            break

        except requests.exceptions.ReadTimeout:
            logger.info("💡 日志流空闲超时，这是正常现象（模型暂时没有新请求）")
            return True  # 超时是正常的，不算失败
        except requests.exceptions.RequestException as e:
            logger.error(f"日志流连接中断: {e}")
            return False

        return True

    def _process_log_entry(self, data: dict):
        """
        处理日志条目

        Args:
            data: 日志数据
        """
        msg_type = data.get('type', 'unknown')

        if msg_type == 'historical':
            # 历史日志
            log = data['log']
            timestamp = datetime.fromtimestamp(log['timestamp']).strftime('%H:%M:%S')
            message = log['message']
            print(f"[{timestamp}] {message}")

        elif msg_type == 'historical_complete':
            # 历史日志发送完成
            logger.info("📜 历史日志发送完成")
            print("=" * 60)
            logger.info("🔄 开始实时日志流...")
            print("=" * 60)

        elif msg_type == 'realtime':
            # 实时日志
            log = data['log']
            timestamp = datetime.fromtimestamp(log['timestamp']).strftime('%H:%M:%S')
            message = log['message']
            print(f"[{timestamp}] {message}")

        elif msg_type == 'stream_end':
            # 流结束
            logger.info("📡 日志流结束")

        elif msg_type == 'error':
            # 错误信息
            logger.error(f"💥 日志流错误: {data.get('message', '未知错误')}")

    def wait_and_connect(self, max_attempts: int = None, interval: int = 5):
        """
        等待并连接到日志流

        Args:
            max_attempts: 最大尝试次数，None表示无限尝试
            interval: 尝试间隔（秒）
        """
        attempt = 0
        self.running = True

        logger.info(f"🚀 开始监控模型 {self.model_alias} 的日志流...")
        logger.info(f"📍 API地址: {self.base_url}")

        try:
            while self.running:
                attempt += 1

                if max_attempts and attempt > max_attempts:
                    logger.error(f"❌ 达到最大尝试次数 {max_attempts}，退出")
                    break

                logger.info(f"📡 尝试连接 #{attempt}...")

                # 首先检查模型状态
                if self.check_model_status():
                    logger.info("✅ 检测到模型已启动")
                    logger.info("🔄 开始连接日志流...")

                    # 尝试连接日志流
                    if self.stream_logs():
                        logger.info("✅ 日志流连接成功并正常工作")
                        return
                    else:
                        logger.warning("⚠️ 日志流连接失败，等待重试...")
                else:
                    logger.info("⏳ 模型未启动，等待中...")

                # 等待下次尝试
                if self.running:
                    logger.info(f"💤 等待 {interval} 秒后重试...")
                    time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("🛑 用户中断")
        finally:
            self.running = False

    def stop(self):
        """停止测试"""
        logger.info("正在停止日志流测试...")
        self.running = False


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='模型日志流测试工具')
    parser.add_argument(
        '--model',
        default='Qwen3-8B-AWQ',
        help='模型别名 (默认: Qwen3-8B-AWQ)'
    )
    parser.add_argument(
        '--url',
        help='API服务器地址 (默认从配置文件读取)'
    )
    parser.add_argument(
        '--max-attempts',
        type=int,
        help='最大尝试次数 (默认无限尝试)'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=5,
        help='尝试间隔秒数 (默认: 5)'
    )
    parser.add_argument(
        '--diagnose',
        action='store_true',
        help='诊断模式 - 只运行一次诊断并退出'
    )

    args = parser.parse_args()

    # 创建测试器
    tester = LogStreamTester(
        model_alias=args.model,
        base_url=args.url or "http://localhost:8000"
    )

    try:
        if args.diagnose:
            # 诊断模式
            logger.info("🔍 运行诊断模式...")
            logger.info("=" * 60)

            # 检查API连接
            try:
                response = tester.session.get(f"{tester.base_url}/health", timeout=5)
                if response.status_code == 200:
                    health = response.json()
                    logger.info(f"✅ API服务器健康状态: {health}")
                else:
                    logger.error(f"❌ API服务器响应异常: {response.status_code}")
                    return
            except Exception as e:
                logger.error(f"❌ 无法连接到API服务器: {e}")
                return

            # 检查模型状态
            logger.info("\n📋 模型状态检查:")
            model_running = tester.check_model_status()

            # 测试流式连接
            logger.info("\n🔄 流式连接测试:")
            stream_success = tester.test_stream_connection()

            # 总结
            logger.info("\n📊 诊断结果:")
            logger.info(f"  模型运行状态: {'✅ 正常' if model_running else '❌ 未运行'}")
            logger.info(f"  流式日志接口: {'✅ 正常' if stream_success else '❌ 异常'}")

            if model_running and stream_success:
                logger.info("\n🎉 流式日志功能正常工作。")
            else:
                logger.info("\n⚠️ 发现问题，请根据上述信息进行排查。")

        else:
            # 正常模式
            tester.wait_and_connect(
                max_attempts=args.max_attempts,
                interval=args.interval
            )

    except KeyboardInterrupt:
        logger.info("测试被用户中断")
    except Exception as e:
        logger.error(f"测试过程中发生错误: {e}")
    finally:
        tester.stop()


if __name__ == "__main__":
    main()