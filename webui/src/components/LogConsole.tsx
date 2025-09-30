import React, { useState, useEffect, useRef } from 'react'
import { apiService } from '../utils/api'
import { LogStreamData } from '../types/api'

interface LogConsoleProps {
  selectedModel: string | null
  height?: string
}

const LogConsole: React.FC<LogConsoleProps> = ({
  selectedModel,
  height = '100%'
}) => {
  const [logs, setLogs] = useState<string[]>([])
  const [isConnected, setIsConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [clearLogs, setClearLogs] = useState(false)
  const logsEndRef = useRef<HTMLDivElement>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  // 清理日志
  useEffect(() => {
    if (clearLogs) {
      setLogs([])
      setClearLogs(false)
    }
  }, [clearLogs])

  // 自动滚动到底部
  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll])

  // 处理日志流连接
  useEffect(() => {
    if (!selectedModel) {
      setIsConnected(false)
      setLogs([])
      if (cleanupRef.current) {
        cleanupRef.current()
        cleanupRef.current = null
      }
      return
    }

    setIsConnected(true)
    setLogs([]) // 切换模型时清空日志

    const handleLogMessage = (data: LogStreamData) => {
      switch (data.type) {
        case 'historical':
          if (data.log && typeof data.log === 'object' && 'timestamp' in data.log) {
            const timestamp = new Date(data.log.timestamp * 1000).toLocaleTimeString()
            setLogs(prev => [...prev, `[${timestamp}] ${data.log.message}`])
          } else if (typeof data.log === 'string') {
            setLogs(prev => [...prev, data.log])
          }
          break
        case 'historical_complete':
          setLogs(prev => [...prev, '[系统] 历史日志加载完成，开始实时监控...'])
          break
        case 'realtime':
          if (data.log && typeof data.log === 'object' && 'timestamp' in data.log) {
            const timestamp = new Date(data.log.timestamp * 1000).toLocaleTimeString()
            setLogs(prev => [...prev, `[${timestamp}] ${data.log.message}`])
          } else if (typeof data.log === 'string') {
            setLogs(prev => [...prev, data.log])
          }
          break
        case 'stream_end':
          setLogs(prev => [...prev, '[系统] 日志流已结束'])
          setIsConnected(false)
          break
        case 'error':
          setLogs(prev => [...prev, `[错误] ${data.message || data.log || '日志流错误'}`])
          setIsConnected(false)
          break
      }
    }

    const handleError = (error: Event) => {
      console.error('Log stream error:', error)
      setLogs(prev => [...prev, `[错误] 日志连接失败`])
      setIsConnected(false)
    }

    const handleClose = () => {
      setIsConnected(false)
    }

    // 建立日志流连接
    cleanupRef.current = apiService.createLogStream(selectedModel, {
      onMessage: handleLogMessage,
      onError: handleError,
      onClose: handleClose
    })

    // 清理函数
    return () => {
      if (cleanupRef.current) {
        cleanupRef.current()
        cleanupRef.current = null
      }
      setIsConnected(false)
    }
  }, [selectedModel])

  const formatLogEntry = (log: string): string => {
    // 日志已经在handleLogMessage中格式化了，直接返回
    return log
  }

  const getLogClass = (log: string): string => {
    const lowerLog = log.toLowerCase()
    if (lowerLog.includes('[info]') || lowerLog.includes('info:')) {
      return 'info'
    } else if (lowerLog.includes('[warning]') || lowerLog.includes('warn:')) {
      return 'warning'
    } else if (lowerLog.includes('[error]') || lowerLog.includes('error:')) {
      return 'error'
    } else if (lowerLog.includes('[success]') || lowerLog.includes('success:')) {
      return 'success'
    }
    return ''
  }

  const handleClearLogs = () => {
    setClearLogs(true)
  }

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const element = e.currentTarget
    const isAtBottom = element.scrollHeight - element.scrollTop <= element.clientHeight + 10
    setAutoScroll(isAtBottom)
  }

  if (!selectedModel) {
    return (
      <div className="log-console-container" style={{ height, width: '100%' }}>
        <div className="log-console-header">
          <h3 className="log-console-title">
            模型控制台
          </h3>
        </div>
        <div className="no-model-selected">
          <div className="no-model-icon">📋</div>
          <div>请选择一个模型查看日志</div>
        </div>
      </div>
    )
  }

  return (
    <div className="log-console-container" style={{ height, width: '100%' }}>
      <div className="log-console-header">
        <h3 className="log-console-title">
          模型控制台 - <span className="selected-model-name">{selectedModel}</span>
          {isConnected && (
            <span className="connection-status" style={{ color: '#34d399', fontSize: '0.7rem' }}>
              ● 已连接
            </span>
          )}
          {!isConnected && selectedModel && (
            <span className="connection-status" style={{ color: '#f87171', fontSize: '0.7rem' }}>
              ● 未连接
            </span>
          )}
        </h3>
        <div className="log-controls">
          <button
            className="log-control-btn"
            onClick={handleClearLogs}
            title="清空日志"
          >
            清空
          </button>
          <button
            className="log-control-btn"
            onClick={() => setAutoScroll(!autoScroll)}
            title={autoScroll ? '关闭自动滚动' : '开启自动滚动'}
          >
            {autoScroll ? '自动滚动: 开' : '自动滚动: 关'}
          </button>
        </div>
      </div>
      <div
        className="log-content"
        onScroll={handleScroll}
      >
        {logs.length === 0 && !isConnected && (
          <div className="no-model-selected">
            <div>正在连接日志流...</div>
          </div>
        )}
        {logs.length === 0 && isConnected && (
          <div className="no-model-selected">
            <div>等待日志数据...</div>
          </div>
        )}
        {logs.map((log, index) => {
          const formattedLog = formatLogEntry(log)
          const logClass = getLogClass(formattedLog)

          return (
            <div key={index} className={`log-entry ${logClass}`}>
              <span className="log-message">{formattedLog}</span>
            </div>
          )
        })}
        <div ref={logsEndRef} />
      </div>
    </div>
  )
}

export default LogConsole