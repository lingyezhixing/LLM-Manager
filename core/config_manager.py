"""
配置管理器 - 负责配置文件的加载、解析和管理
从ModelController中独立出来的配置管理功能
"""

import json
import threading
import logging
import os
from typing import Dict, List, Optional, Any, Set
from utils.logger import get_logger

logger = get_logger(__name__)

class ConfigManager:
    """配置管理器"""

    def __init__(self, config_path: str = 'config.json'):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.alias_to_primary_name: Dict[str, str] = {}
        self.config_lock = threading.Lock()
        self.load_config()

    def load_config(self):
        """加载配置文件"""
        with self.config_lock:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                self._init_alias_mapping()
                logger.info(f"配置文件加载成功: {self.config_path}")
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}")
                raise

    def reload_config(self):
        """重新加载配置文件"""
        logger.info("重新加载配置文件...")
        self.load_config()

    def _init_alias_mapping(self):
        """初始化别名映射"""
        self.alias_to_primary_name.clear()
        all_aliases_check = set()

        if "program" not in self.config:
            raise ValueError("配置文件中缺少 'program' 部分")

        for key, model_cfg in self.config.items():
            # 跳过非模型配置部分
            if key in ["program"]:
                continue

            aliases = model_cfg.get("aliases")
            if not isinstance(aliases, list) or not aliases:
                raise ValueError(f"模型配置 '{key}' 缺少 'aliases' 或其为空列表")

            primary_name = aliases[0]
            for alias in aliases:
                if alias in all_aliases_check:
                    raise ValueError(f"配置错误: 别名 '{alias}' 重复")
                all_aliases_check.add(alias)
                self.alias_to_primary_name[alias] = primary_name

    def get_program_config(self) -> Dict[str, Any]:
        """获取程序配置"""
        return self.config.get("program", {})

    def get_model_config(self, alias: str) -> Optional[Dict[str, Any]]:
        """获取模型配置"""
        primary_name = self.alias_to_primary_name.get(alias)
        if not primary_name:
            return None

        for key, model_cfg in self.config.items():
            # 跳过非模型配置部分
            if key in ["program"]:
                continue
            if model_cfg.get("aliases", []) and model_cfg["aliases"][0] == primary_name:
                return model_cfg
        return None

    def get_all_model_configs(self) -> Dict[str, Dict[str, Any]]:
        """获取所有模型配置"""
        model_configs = {}
        for key, model_cfg in self.config.items():
            if key not in ["program"] and model_cfg.get("aliases"):
                primary_name = model_cfg["aliases"][0]
                model_configs[primary_name] = model_cfg
        return model_configs

    def get_model_names(self) -> List[str]:
        """获取所有模型主名称"""
        return list(self.get_all_model_configs().keys())

    def resolve_primary_name(self, alias: str) -> str:
        """解析模型别名为主名称"""
        primary_name = self.alias_to_primary_name.get(alias)
        if not primary_name:
            raise KeyError(f"无法解析模型别名 '{alias}'")
        return primary_name

    def get_all_aliases(self) -> Dict[str, str]:
        """获取所有别名映射"""
        return self.alias_to_primary_name.copy()

    def get_adaptive_model_config(self, alias: str, online_devices: Set[str]) -> Optional[Dict[str, Any]]:
        """
        根据当前设备状态获取自适应模型配置
        按优先级顺序尝试不同的配置方案
        """
        base_config = self.get_model_config(alias)
        if not base_config:
            return None

        logger.debug(f"模型 '{alias}' 启动时检测到在线设备: {online_devices}")

        # 按优先级顺序尝试不同的配置方案
        priority_configs = []
        for key in base_config.keys():
            if key not in ["aliases", "mode", "port", "auto_start"]:
                config_data = base_config[key]
                if isinstance(config_data, dict) and "required_devices" in config_data:
                    priority_configs.append((key, config_data))

        # 按照配置文件中的顺序进行尝试
        for config_name, config_data in priority_configs:
            required_devices = set(config_data.get("required_devices", []))

            if required_devices.issubset(online_devices):
                logger.debug(f"模型 '{alias}' 使用配置方案: {config_name}，需要设备: {required_devices}")

                # 构建完整的自适应配置
                adaptive_config = base_config.copy()

                # 移除旧的配置键
                for key in list(adaptive_config.keys()):
                    if key not in ["aliases", "mode", "port", "auto_start"]:
                        del adaptive_config[key]

                # 添加新的配置值
                adaptive_config.update({
                    "bat_path": config_data["bat_path"],
                    "memory_mb": config_data["memory_mb"],
                    "config_source": config_name
                })

                return adaptive_config

        logger.warning(f"模型 '{alias}' 没有找到适合当前设备状态 {online_devices} 的配置方案")
        return None

    def get_device_plugin_dir(self) -> str:
        """获取设备插件目录"""
        return self.get_program_config().get('device_plugin_dir', 'plugins/devices')

    def get_interface_plugin_dir(self) -> str:
        """获取接口插件目录"""
        return self.get_program_config().get('interface_plugin_dir', 'plugins/interfaces')

    def get_openai_config(self) -> Dict[str, Any]:
        """获取OpenAI API配置"""
        return {
            "host": self.get_program_config().get('openai_host', '0.0.0.0'),
            "port": self.get_program_config().get('openai_port', 8080)
        }

    def get_webui_config(self) -> Dict[str, Any]:
        """获取WebUI前端配置"""
        return {
            "host": self.get_program_config().get('webui_host', '127.0.0.1'),
            "port": self.get_program_config().get('webui_port', 10000)
        }

    def get_alive_time(self) -> int:
        """获取模型存活时间（分钟）"""
        return self.get_program_config().get('alive_time', 0)

    def get_log_level(self) -> str:
        """获取日志级别"""
        return self.get_program_config().get('log_level', 'INFO')

    def is_gpu_monitoring_disabled(self) -> bool:
        """是否禁用GPU监控"""
        return self.get_program_config().get('Disable_GPU_monitoring', False)

    def get_model_port(self, alias: str) -> Optional[int]:
        """获取模型端口"""
        config = self.get_model_config(alias)
        return config.get("port") if config else None

    def get_model_mode(self, alias: str) -> str:
        """获取模型模式"""
        config = self.get_model_config(alias)
        return config.get("mode", "Chat") if config else "Chat"

    def is_auto_start(self, alias: str) -> bool:
        """检查模型是否自动启动"""
        config = self.get_model_config(alias)
        return config.get("auto_start", False) if config else False

    def validate_config(self) -> List[str]:
        """验证配置文件的有效性"""
        errors = []

        try:
            # 检查必需的程序配置
            program_config = self.get_program_config()
            required_program_keys = ['openai_host', 'openai_port']
            for key in required_program_keys:
                if key not in program_config:
                    errors.append(f"缺少必需的程序配置项: {key}")

            # 检查模型配置
            for key, model_cfg in self.config.items():
                if key in ["program"]:
                    continue

                # 检查必需的模型配置项
                required_model_keys = ['aliases', 'mode', 'port']
                for req_key in required_model_keys:
                    if req_key not in model_cfg:
                        errors.append(f"模型 '{key}' 缺少必需配置项: {req_key}")

                # 检查别名
                aliases = model_cfg.get('aliases', [])
                if not aliases or not isinstance(aliases, list):
                    errors.append(f"模型 '{key}' 的aliases配置无效")

                # 检查设备配置
                has_device_config = False
                for cfg_key in model_cfg.keys():
                    if cfg_key not in ["aliases", "mode", "port", "auto_start"]:
                        device_config = model_cfg[cfg_key]
                        if isinstance(device_config, dict):
                            has_device_config = True
                            # 检查必需的设备配置项
                            required_device_keys = ['required_devices', 'bat_path', 'memory_mb']
                            for req_key in required_device_keys:
                                if req_key not in device_config:
                                    errors.append(f"模型 '{key}' 的设备配置 '{cfg_key}' 缺少必需项: {req_key}")

                if not has_device_config:
                    errors.append(f"模型 '{key}' 没有有效的设备配置")

        except Exception as e:
            errors.append(f"配置验证失败: {str(e)}")

        return errors

    def save_config(self, backup: bool = True) -> bool:
        """保存配置文件"""
        try:
            if backup and os.path.exists(self.config_path):
                backup_path = f"{self.config_path}.backup"
                import shutil
                shutil.copy2(self.config_path, backup_path)
                logger.info(f"配置文件已备份到: {backup_path}")

            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)

            logger.info(f"配置文件保存成功: {self.config_path}")
            return True

        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False

    def update_model_config(self, alias: str, new_config: Dict[str, Any]) -> bool:
        """更新模型配置"""
        try:
            primary_name = self.resolve_primary_name(alias)

            # 找到并更新配置
            for key, model_cfg in self.config.items():
                if key not in ["program"] and model_cfg.get("aliases", []) and model_cfg["aliases"][0] == primary_name:
                    self.config[key].update(new_config)
                    break

            # 重新初始化别名映射
            self._init_alias_mapping()
            return True

        except Exception as e:
            logger.error(f"更新模型配置失败: {e}")
            return False