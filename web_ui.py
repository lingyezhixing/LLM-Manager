# web_ui.py
import gradio as gr
import logging
import time
from model_manager import ModelManager
from gpu_utils import get_gpu_info
import os
logger = logging.getLogger(__name__)
model_manager_instance: ModelManager = None
UI_REFRESH_INTERVAL = 2
LOG_REFRESH_INTERVAL = 1
def get_gpu_status():
    try:
        gpus = get_gpu_info()
        if not gpus: return [["N/A", "未检测到NVIDIA GPU", "N/A", "N/A"]]
        return [
            [gpu.id, gpu.name, f"{gpu.memoryUsed:.0f} / {gpu.memoryTotal:.0f} MB", f"{gpu.load * 100:.1f}%"]
            for gpu in gpus
        ]
    except Exception as e:
        logger.error(f"WebUI获取GPU信息失败: {e}")
        return []
def refresh_ui_data():
    while True:
        try:
            gpu_status = get_gpu_status()
            all_model_status = model_manager_instance.get_all_models_status()
            
            active_models = [name for name, data in all_model_status.items() if data['status'] != 'stopped']
            log_dropdown_update = gr.update(choices=active_models if active_models else ["无活动模型"])
            
            status_updates = []
            requests_updates = []
            for name, data in sorted(all_model_status.items()):
                status_updates.append(data['status'].capitalize())
                requests_updates.append(data['pending_requests'])
            
            yield (gpu_status, log_dropdown_update,
                   *status_updates, *requests_updates)
            
        except Exception as e:
            logger.error(f"UI刷新循环出错: {e}")
            yield (get_gpu_status() or []), gr.update(choices=["刷新出错"]), *([""] * 100)
        time.sleep(UI_REFRESH_INTERVAL)

def get_current_log_model_and_output(log_model_select_value, active_models):
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
            log_lines = model_manager_instance.get_model_log(selected_model)
            log_output = "\n".join(log_lines)
        except Exception as e:
            logger.error(f"获取日志时出错 {selected_model}: {e}")
            log_output = f"获取 {selected_model} 日志时出错。"
    
    return selected_model, log_output
def stream_log_output(model_name: str):
    if not model_name or model_name == "无活动模型":
        yield "请从上方选择一个模型以查看其日志。"
        return
    while True:
        try:
            log_lines = model_manager_instance.get_model_log(model_name)
            yield "\n".join(log_lines)
        except Exception as e:
            logger.error(f"刷新日志时出错 {model_name}: {e}")
            yield f"获取 {model_name} 日志时出错。"
        
        time.sleep(LOG_REFRESH_INTERVAL)
def create_model_control_row(primary_name: str, status_data: dict, index: int):
    with gr.Row(variant="panel"):
        with gr.Column(scale=3):
            gr.Markdown(f"**{primary_name}**")
            mode_display = status_data.get('mode', 'Chat')
            mode_emoji = {"Chat": "💬", "Base": "📝", "Embedding": "🔍"}.get(mode_display, "🤖")
            gr.Markdown(f"<small>{mode_emoji} 模式: {mode_display}</small>")
            if status_data['aliases'][1:]:
                aliases_str = ", ".join(status_data['aliases'][1:])
                gr.Markdown(f"<small>别名: {aliases_str}</small>")
        
        # 修改点1：将状态和请求放在同一行的两个框中
        with gr.Column(scale=5, min_width=400):  # 增加宽度
            with gr.Row():
                status_box = gr.Textbox(
                    value=status_data['status'].capitalize(),
                    label="状态",
                    interactive=False,
                    scale=1,
                    elem_id=f"status_box_{index}",
                    min_width=200  # 增加最小宽度
                )
                requests_box = gr.Textbox(
                    value=status_data['pending_requests'],
                    label="待处理请求",
                    interactive=False,
                    scale=1,
                    elem_id=f"requests_box_{index}",
                    min_width=200  # 增加最小宽度
                )
        
        with gr.Column(scale=3, min_width=240):
            with gr.Row():
                start_btn = gr.Button("启动", variant="primary", size="sm")
                stop_btn = gr.Button("停止", variant="stop", size="sm")
    
    start_btn.click(
        fn=lambda p_name=primary_name: model_manager_instance.start_model(
            alias=p_name, 
            bypass_vram_check=model_manager_instance.get_model_config(p_name).get("bypass_vram_check", False)
        ),
        inputs=[], 
        outputs=[]
    ).then(lambda: gr.Info(f"已发送启动 '{primary_name}' 的指令..."), None, None)
    stop_btn.click(
        fn=lambda p_name=primary_name: model_manager_instance.stop_model(alias=p_name),
        inputs=None, 
        outputs=None
    ).then(lambda: gr.Info(f"已发送停止 '{primary_name}' 的指令..."), None, None)
    
    return status_box, requests_box
def run_web_ui(manager: ModelManager, host: str, port: int):
    global model_manager_instance
    model_manager_instance = manager
    
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
        
        /* 固定控制台输出区域高度并启用滚动 */
        .console-output .gr-textbox {
            height: 600px !important;
            max-height: 600px !important;
            overflow-y: auto !important;
            flex-grow: 0 !important;
            flex-shrink: 0 !important;
            width: 100% !important;
            min-width: 100% !important;
        }
        
        /* 控制台输出区域滚动条样式 */
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
        
        /* 强制限制控制台输出区域容器高度 */
        .console-output {
            height: 600px !important;
            max-height: 600px !important;
            overflow-y: hidden !important;  /* 改为hidden，避免双层滚动条 */
            flex-grow: 0 !important;
            flex-shrink: 0 !important;
            width: 100% !important;
            min-width: 100% !important;
        }
        
        /* 确保控制台输出区域的父容器不会扩展 */
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
            height: 600px !important;  /* 减少模型面板高度 */
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
        
        /* 确保模型面板和日志面板高度一致 */
        .panel-content {
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        /* 移除之前的高度限制，使用新的样式 */
        .log-panel .gr-box {
            height: auto !important;
        }
        
        /* 强制限制日志面板中的所有元素高度 */
        .log-panel .gradio-row {
            flex-grow: 0 !important;
            flex-shrink: 0 !important;
            width: 100% !important;
        }
        
        /* 确保日志面板中的最后一行（包含控制台输出）不会扩展 */
        .log-panel .panel-content > :last-child {
            flex-grow: 0 !important;
            flex-shrink: 0 !important;
            width: 100% !important;
        }
        
        /* 强制限制整个日志面板的高度 */
        .log-panel {
            max-height: 800px !important;
            overflow: hidden !important;
            width: 100% !important;
        }
        
        /* 确保控制台输出区域宽度正常 */
        .console-output-wrapper .gradio-row {
            width: 100% !important;
            min-width: 100% !important;
        }
        
        /* 确保控制台输出区域的父容器宽度正常 */
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
                gr.Markdown("## 🖥️ GPU 实时监控")
                gpu_table = gr.DataFrame(
                    headers=["ID", "名称", "显存使用", "占用率"],
                    datatype=["str", "str", "str", "str"],
                    interactive=False,
                    row_count=(len(get_gpu_info()) or 1, "fixed")
                )
        
        with gr.Row():
            # 修改点3：调整列比例，模型面板占比更大
            with gr.Column(scale=1, elem_classes="model-panel"):
                gr.Markdown("## 🚀 模型控制面板")
                gr.Markdown("<small>面板中的模型状态和请求数会实时更新。</small>")
                
                with gr.Column(elem_classes="panel-content"):
                    with gr.Column(elem_classes="model-scroll-container"):
                        all_model_status = manager.get_all_models_status()
                        if not all_model_status:
                            gr.Markdown("❌ **错误**: `config.json` 中没有找到任何模型配置。")
                        else:
                            sorted_models = sorted(all_model_status.items())
                            for i, (name, data) in enumerate(sorted_models):
                                status_box, requests_box = create_model_control_row(name, data, i)
                                status_boxes.append(status_box)
                                requests_boxes.append(requests_box)
            
            # 修改点4：日志面板占比更小
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
                        # 修改点5：固定控制台高度并启用滚动
                        log_output = gr.Textbox(
                            label="控制台输出",
                            lines=30,  # 增加行数以适应固定高度
                            interactive=False,
                            max_lines=2000,
                            autoscroll=False,  # 禁用自动滚动，让用户控制滚动位置
                            elem_classes="console-output"
                        )
        
        ui.load(
            fn=refresh_ui_data,
            inputs=None,
            outputs=[gpu_table, log_model_select] + status_boxes + requests_boxes
        )
        
        # 添加一个函数来处理模型切换时的提示
        def show_refresh_hint():
            """显示刷新提示"""
            return "⚠️ 模型状态已变更，请手动刷新页面以更新日志显示选项。"
        
        # 修改UI刷新逻辑，当活动模型变更时显示提示
        ui.load(
            fn=refresh_ui_data,
            inputs=None,
            outputs=[gpu_table, log_model_select] + status_boxes + requests_boxes
        )
        
        log_model_select.change(
            fn=stream_log_output,
            inputs=log_model_select,
            outputs=log_output,
            show_progress="hidden"
        )
        
    logger.info(f"WebUI 管理界面将在 http://{host}:{port} 上启动")
    # 修改点5：确保控制台使用chcp 65001适配中文
    os.environ["GRADIO_SERVER_NAME"] = host
    os.environ["GRADIO_SERVER_PORT"] = str(port)
    # 在启动前设置控制台编码
    os.system("chcp 65001 >nul")
    ui.queue().launch(server_name=host, server_port=port, share=False)