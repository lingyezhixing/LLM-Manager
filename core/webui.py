import gradio as gr
import logging
import time
from typing import Dict, List, Any, Tuple
from utils.logger import get_logger
from core.model_controller import ModelController

logger = get_logger(__name__)

class WebUI:
    """WebUI服务 - 提供Web管理界面"""

    def __init__(self, model_controller: ModelController):
        self.model_controller = model_controller
        self.model_manager_instance = model_controller
        self.UI_REFRESH_INTERVAL = 2
        self.LOG_REFRESH_INTERVAL = 1

    def get_gpu_status(self) -> List[List[str]]:
        """获取GPU状态"""
        try:
            gpu_status = []
            for device_name, device_plugin in self.model_controller.device_plugins.items():
                if device_plugin.is_online():
                    total_mb, available_mb, used_mb = device_plugin.get_memory_info()
                    gpu_status.append([
                        device_name,
                        f"{used_mb}/{total_mb} MB",
                        f"{available_mb} MB 可用"
                    ])

            if not gpu_status:
                return [["N/A", "未检测到设备", "N/A"]]

            return gpu_status
        except Exception as e:
            logger.error(f"WebUI获取设备信息失败: {e}")
            return []

    def refresh_ui_data(self):
        """刷新UI数据"""
        while True:
            try:
                gpu_status = self.get_gpu_status()
                all_model_status = self.model_controller.get_all_models_status()

                active_models = [
                    name for name, data in all_model_status.items()
                    if data['status'] not in ['stopped', 'failed']
                ]

                log_dropdown_update = gr.update(
                    choices=active_models if active_models else ["无活动模型"]
                )

                status_updates = []
                requests_updates = []

                for name, data in sorted(all_model_status.items()):
                    status_updates.append(data['status'].capitalize())
                    requests_updates.append(data['pending_requests'])

                yield (
                    gpu_status,
                    log_dropdown_update,
                    *status_updates,
                    *requests_updates
                )

            except Exception as e:
                logger.error(f"UI刷新循环出错: {e}")
                yield (
                    self.get_gpu_status() or [],
                    gr.update(choices=["刷新出错"]),
                    *([""] * 100)
                )

            time.sleep(self.UI_REFRESH_INTERVAL)

    def get_current_log_model_and_output(self, log_model_select_value: str, active_models: List[str]) -> Tuple[str, str]:
        """获取当前应该显示的日志模型和对应的日志输出"""
        if not active_models:
            return "无活动模型", "请从上方选择一个模型以查看其日志。"

        # 如果当前选择的模型不在活动模型列表中，则选择第一个活动模型
        if log_model_select_value not in active_models and log_model_select_value != "无活动模型":
            selected_model = active_models[0] if active_models else "无活动模型"
        else:
            selected_model = log_model_select_value

        # 获取选中模型的日志
        if selected_model == "无活动模型":
            log_output = "请从上方选择一个模型以查看其日志。"
        else:
            try:
                log_lines = self.model_controller.get_model_log(selected_model)
                log_output = "\n".join(log_lines)
            except Exception as e:
                logger.error(f"获取日志时出错 {selected_model}: {e}")
                log_output = f"获取 {selected_model} 日志时出错。"

        return selected_model, log_output

    def stream_log_output(self, model_name: str):
        """流式输出日志"""
        if not model_name or model_name == "无活动模型":
            yield "请从上方选择一个模型以查看其日志。"
            return

        while True:
            try:
                log_lines = self.model_controller.get_model_log(model_name)
                yield "\n".join(log_lines)
            except Exception as e:
                logger.error(f"刷新日志时出错 {model_name}: {e}")
                yield f"获取 {model_name} 日志时出错。"

            time.sleep(self.LOG_REFRESH_INTERVAL)

    def create_model_control_row(self, primary_name: str, status_data: dict, index: int):
        """创建模型控制行"""
        with gr.Row(variant="panel"):
            with gr.Column(scale=3):
                gr.Markdown(f"**{primary_name}**")

                mode_display = status_data.get('mode', 'Chat')
                mode_emoji = {
                    "Chat": "💬",
                    "Base": "📝",
                    "Embedding": "🔍",
                    "Reranker": "🔄"
                }.get(mode_display, "🤖")

                gr.Markdown(f"<small>{mode_emoji} 模式: {mode_display}</small>")

                if status_data['aliases'][1:]:
                    aliases_str = ", ".join(status_data['aliases'][1:])
                    gr.Markdown(f"<small>别名: {aliases_str}</small>")

            with gr.Column(scale=5, min_width=400):
                with gr.Row():
                    status_box = gr.Textbox(
                        value=status_data['status'].capitalize(),
                        label="状态",
                        interactive=False,
                        scale=1,
                        elem_id=f"status_box_{index}",
                        min_width=200
                    )
                    requests_box = gr.Textbox(
                        value=status_data['pending_requests'],
                        label="待处理请求",
                        interactive=False,
                        scale=1,
                        elem_id=f"requests_box_{index}",
                        min_width=200
                    )

            with gr.Column(scale=3, min_width=240):
                with gr.Row():
                    start_btn = gr.Button("启动", variant="primary", size="sm")
                    stop_btn = gr.Button("停止", variant="stop", size="sm")

        start_btn.click(
            fn=lambda p_name=primary_name: self.model_controller.start_model(alias=p_name),
            inputs=[],
            outputs=[]
        ).then(lambda: gr.Info(f"已发送启动 '{primary_name}' 的指令..."), None, None)

        stop_btn.click(
            fn=lambda p_name=primary_name: self.model_controller.stop_model(alias=p_name),
            inputs=None,
            outputs=None
        ).then(lambda: gr.Info(f"已发送停止 '{primary_name}' 的指令..."), None, None)

        return status_box, requests_box

    def run(self, host: str, port: int):
        """运行WebUI服务"""
        custom_css = """
            .console-output textarea {
                background-color: #000000 !important;
                color: #ffffff !important;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace !important;
                font-size: 14px !important;
                line-height: 1.4 !important;
                border: 1px solid #333333 !important;
                border-radius: 4px !important;
                resize: none !important;
                height: 580px !important;
                max-height: 580px !important;
                overflow-y: auto !important;
                width: 100% !important;
                min-width: 100% !important;
            }

            .console-output .gr-textbox {
                height: 600px !important;
                max-height: 600px !important;
                overflow-y: auto !important;
                flex-grow: 0 !important;
                flex-shrink: 0 !important;
                width: 100% !important;
                min-width: 100% !important;
            }

            .console-output .gr-textbox::-webkit-scrollbar {
                width: 12px !important;
            }

            .console-output .gr-textbox::-webkit-scrollbar-thumb {
                background: #888 !important;
                border-radius: 6px !important;
            }

            .console-output .gr-textbox::-webkit-scrollbar-thumb:hover {
                background: #555 !important;
            }

            .console-output {
                height: 600px !important;
                max-height: 600px !important;
                overflow-y: hidden !important;
                flex-grow: 0 !important;
                flex-shrink: 0 !important;
                width: 100% !important;
                min-width: 100% !important;
            }

            .console-output-wrapper {
                height: 600px !important;
                max-height: 600px !important;
                overflow: hidden !important;
                flex-grow: 0 !important;
                flex-shrink: 0 !important;
                width: 100% !important;
                min-width: 100% !important;
            }

            .model-scroll-container {
                height: 600px !important;
                overflow-y: auto !important;
                padding-right: 10px !important;
                border: 1px solid #e0e0e0 !important;
                border-radius: 8px !important;
                background-color: #f8f9fa !important;
            }

            .model-scroll-container::-webkit-scrollbar {
                width: 12px !important;
            }

            .model-scroll-container::-webkit-scrollbar-thumb {
                background: #888 !important;
                border-radius: 6px !important;
            }

            .model-scroll-container::-webkit-scrollbar-thumb:hover {
                background: #555 !important;
            }

            #main-title {
                text-align: center;
                margin-bottom: 20px !important;
            }

            .gpu-monitor {
                margin-bottom: 20px;
                padding: 15px;
            }

            .model-panel {
                padding: 15px;
                height: 100%;
                display: flex;
                flex-direction: column;
            }

            .log-panel {
                padding: 15px;
                height: 100%;
                display: flex;
                flex-direction: column;
            }

            .panel-content {
                flex: 1;
                display: flex;
                flex-direction: column;
            }

            .log-panel .gr-box {
                height: auto !important;
            }

            .log-panel .gradio-row {
                flex-grow: 0 !important;
                flex-shrink: 0 !important;
                width: 100% !important;
            }

            .log-panel .panel-content > :last-child {
                flex-grow: 0 !important;
                flex-shrink: 0 !important;
                width: 100% !important;
            }

            .log-panel {
                max-height: 800px !important;
                overflow: hidden !important;
                width: 100% !important;
            }

            .console-output-wrapper .gradio-row {
                width: 100% !important;
                min-width: 100% !important;
            }

            .console-output-wrapper {
                width: 100% !important;
                min-width: 100% !important;
            }

            .gradio-container {
                background-color: inherit !important;
            }
        """

        with gr.Blocks(title="LLM-Manager", theme=gr.themes.Soft(), css=custom_css) as ui:
            gr.Markdown("# 🧠 LLM-Manager 控制台", elem_id="main-title")

            status_boxes = []
            requests_boxes = []

            with gr.Row():
                with gr.Column(elem_classes="gpu-monitor"):
                    gr.Markdown("## 🖥️ 设备实时监控")
                    gpu_table = gr.DataFrame(
                        headers=["设备名称", "内存使用", "可用内存"],
                        datatype=["str", "str", "str"],
                        interactive=False,
                        row_count=(1, "fixed")
                    )

            with gr.Row():
                with gr.Column(scale=1, elem_classes="model-panel"):
                    gr.Markdown("## 🚀 模型控制面板")
                    gr.Markdown("<small>面板中的模型状态和请求数会实时更新。</small>")

                    with gr.Column(elem_classes="panel-content"):
                        with gr.Column(elem_classes="model-scroll-container"):
                            all_model_status = self.model_controller.get_all_models_status()
                            if not all_model_status:
                                gr.Markdown("❌ **错误**: 配置文件中没有找到任何模型配置。")
                            else:
                                sorted_models = sorted(all_model_status.items())
                                for i, (name, data) in enumerate(sorted_models):
                                    status_box, requests_box = self.create_model_control_row(name, data, i)
                                    status_boxes.append(status_box)
                                    requests_boxes.append(requests_box)

                with gr.Column(scale=3, elem_classes="log-panel"):
                    gr.Markdown("## 📜 模型实时日志")
                    gr.Markdown("<small>⚠️ 注意：当启动或停止模型后，请手动刷新页面以更新日志显示选项。</small>")

                    with gr.Column(elem_classes="panel-content"):
                        with gr.Row():
                            log_model_select = gr.Dropdown(
                                label="选择一个活动中的模型查看日志",
                                choices=["无活动模型"],
                                container=False
                            )

                        with gr.Row(elem_classes="console-output-wrapper"):
                            log_output = gr.Textbox(
                                label="控制台输出",
                                lines=30,
                                interactive=False,
                                max_lines=2000,
                                autoscroll=False,
                                elem_classes="console-output"
                            )

            ui.load(
                fn=self.refresh_ui_data,
                inputs=None,
                outputs=[gpu_table, log_model_select] + status_boxes + requests_boxes
            )

            log_model_select.change(
                fn=self.stream_log_output,
                inputs=log_model_select,
                outputs=log_output,
                show_progress="hidden"
            )

        logger.info(f"WebUI 管理界面将在 http://{host}:{port} 上启动")

        # 设置环境变量
        import os
        os.environ["GRADIO_SERVER_NAME"] = host
        os.environ["GRADIO_SERVER_PORT"] = str(port)
        os.system("chcp 65001 >nul")

        ui.queue().launch(server_name=host, server_port=port, share=False)

def run_web_ui(model_controller: ModelController, host: str, port: int):
    """运行WebUI的便捷函数"""
    webui = WebUI(model_controller)
    webui.run(host, port)