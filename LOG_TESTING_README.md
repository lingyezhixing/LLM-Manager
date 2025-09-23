# LLM-Manager 日志流测试工具

本目录包含用于测试 LLM-Manager 日志流功能的工具脚本。

## 功能特性

### 1. 基础测试工具 (`test_log_stream.py`)

**主要功能：**
- 持续尝试连接指定模型的日志流
- 自动检测模型启动状态
- 输出全部历史日志后开始实时流式推送
- 支持彩色日志输出，不同级别用不同颜色显示
- 自动从配置文件读取API地址

**使用方法：**
```bash
# 基础用法（测试Qwen3-30B-A3B-Instruct-2507模型）
python test_log_stream.py

# 指定模型
python test_log_stream.py --model YourModelName

# 指定API地址
python test_log_stream.py --url http://localhost:8000

# 限制最大尝试次数
python test_log_stream.py --max-attempts 10

# 调整重试间隔
python test_log_stream.py --interval 3
```

**快速启动：**
```bash
# Windows用户可以直接运行批处理文件
test_logs.bat
```

### 2. 高级测试工具 (`advanced_log_tester.py`)

**主要功能：**
- 支持同时监控多个模型
- 实时统计信息显示
- 交互式操作界面
- 详细的连接状态监控
- 错误统计和分析

**使用方法：**
```bash
# 监控单个模型（默认）
python advanced_log_tester.py

# 监控多个模型
python advanced_log_tester.py --models Model1 Model2 Model3

# 使用交互模式
python advanced_log_tester.py --mode interactive

# 指定API地址
python advanced_log_tester.py --url http://localhost:8001
```

**交互模式命令：**
- `stats` - 显示统计信息
- `logs <模型名>` - 显示指定模型的实时日志
- `clear` - 清空屏幕
- `help` - 显示帮助
- `quit` - 退出

## 输出格式说明

### 颜色编码
- 🔴 **ERROR** - 红色，错误信息
- 🟡 **WARNING** - 黄色，警告信息
- 🟢 **SUCCESS** - 绿色，成功信息
- ⚪ **INFO** - 白色，普通信息

### 日志类型
- `[历史]` - 模型启动前的历史日志
- `[实时]` - 实时流式推送的日志

### 状态指示器
- 🟢 已连接
- 🟡 连接中
- 🔴 模型未运行
- ⚫ 连接断开

## API接口

### 新增的日志接口

1. **流式日志接口**
   ```
   GET /api/models/{model_alias}/logs/stream
   ```
   - 返回 Server-Sent Events 格式的流式数据
   - 先发送历史日志，标记类型为 `historical`
   - 发送完成标记，类型为 `historical_complete`
   - 然后开始实时推送，类型为 `realtime`

2. **日志统计接口**
   ```
   GET /api/logs/stats
   ```
   - 返回日志统计信息
   - 包括总日志数、订阅者数量等

3. **清空日志接口**
   ```
   POST /api/logs/{model_alias}/clear
   ```
   - 清空指定模型的日志

## 使用场景

### 1. 模型启动监控
```bash
# 启动监控，等待模型启动
python test_log_stream.py --max-attempts 0
```

### 2. 问题诊断
```bash
# 使用高级工具监控多个模型，查看错误统计
python advanced_log_tester.py --mode interactive
```

### 3. 性能测试
```bash
# 长时间监控，观察日志流稳定性
python test_log_stream.py --interval 1
```

## 故障排除

### 常见问题

1. **连接被拒绝**
   - 检查 LLM-Manager 是否正在运行
   - 确认端口号是否正确
   - 检查防火墙设置

2. **模型未启动**
   - 确认模型名称是否正确
   - 检查模型配置文件
   - 手动启动模型进行测试

3. **日志流中断**
   - 检查网络连接
   - 查看服务器日志
   - 重新运行测试脚本

### 调试模式

启用详细日志输出：
```bash
python test_log_stream.py 2>&1 | tee debug.log
```

## 技术细节

### 协议说明
- 使用 **Server-Sent Events (SSE)** 协议
- 数据格式为 JSON
- 每条消息以 `data: ` 开头，以 `\n\n` 结尾

### 数据结构
```json
{
  "type": "historical|realtime|historical_complete|stream_end|error",
  "log": {
    "timestamp": 1634567890,
    "level": "info|warning|error|success",
    "message": "日志内容"
  },
  "message": "错误信息（仅error类型）"
}
```

### 性能特性
- 内存存储，无磁盘I/O
- 支持多个并发订阅者
- 线程安全设计
- 自动清理断开连接的订阅者

## 更新日志

### v1.0.0 (2024-01-XX)
- ✅ 实现基础日志流功能
- ✅ 添加历史日志推送
- ✅ 实现实时日志流
- ✅ 添加测试工具脚本
- ✅ 支持彩色输出和状态监控