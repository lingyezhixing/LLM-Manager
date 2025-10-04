import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import { ModelInfo, ModelActionResponse } from '../types/api'

interface ModelCardProps {
  modelInfo: ModelInfo
  isSelected: boolean
  onClick: () => void
  onStatusChange?: () => void
}

const ModelCard: React.FC<ModelCardProps> = ({
  modelInfo,
  isSelected,
  onClick,
  onStatusChange
}) => {
  const [currentInfo, setCurrentInfo] = useState<ModelInfo>(modelInfo)
  const [isOperating, setIsOperating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  // 移除独立的刷新逻辑，改为由父组件统一管理
  useEffect(() => {
    // 当父组件更新props时，检查状态变化并更新本地状态
    const prevStatus = currentInfo.status

    // 当模型状态发生变化时，清除成功消息
    if (modelInfo.status !== prevStatus && successMessage) {
      setSuccessMessage(null)
    }

    // 更新本地状态
    setCurrentInfo(modelInfo)
  }, [modelInfo])

  const handleStart = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (isOperating) return

    setIsOperating(true)
    setError(null)
    setSuccessMessage(null)

    // 使用第一个可用的别名，或者model_name
    const modelAlias = currentInfo.aliases?.[0] || currentInfo.model_name

    if (!modelAlias) {
      setError('模型别名未找到')
      setIsOperating(false)
      return
    }

    try {
      const response: ModelActionResponse = await apiService.startModel(modelAlias)

      // 根据API返回的message判断是否启动成功
      // 不论成功失败，都设置操作状态为false，让定时刷新来更新状态
      setIsOperating(false)

      if (response.success) {
        // 启动命令发送成功，显示成功消息
        setSuccessMessage(response.message || '启动命令已发送')
        // 不设置错误，让3秒定时刷新来更新实际状态
      } else {
        // 启动命令发送失败，显示错误消息
        setError(response.message || '启动命令发送失败')
      }
    } catch (err) {
      console.error('Failed to start model:', err)
      setError('启动命令发送失败')
    }
  }

  const handleStop = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (isOperating) return

    setIsOperating(true)
    setError(null)
    setSuccessMessage(null)

    // 使用第一个可用的别名，或者model_name
    const modelAlias = currentInfo.aliases?.[0] || currentInfo.model_name

    if (!modelAlias) {
      setError('模型别名未找到')
      setIsOperating(false)
      return
    }

    try {
      const response: ModelActionResponse = await apiService.stopModel(modelAlias)

      // 根据API返回的message判断是否停止成功
      // 不论成功失败，都设置操作状态为false，让定时刷新来更新状态
      setIsOperating(false)

      if (response.success) {
        // 停止命令发送成功，显示成功消息
        setSuccessMessage(response.message || '停止命令已发送')
        console.log('停止命令已发送:', response.message)
        // 不设置错误，让3秒定时刷新来更新实际状态
      } else {
        // 停止命令发送失败，显示错误消息
        setError(response.message || '停止命令发送失败')
      }
    } catch (err) {
      console.error('Failed to stop model:', err)
      setError('停止命令发送失败')
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'routing':
        return 'online'
      case 'starting':
        return 'starting'
      case 'stopped':
        return 'offline'
      case 'failed':
        return 'failed'
      default:
        return 'offline'
    }
  }

  const getStatusText = (status: string) => {
    switch (status) {
      case 'routing':
        return '运行中'
      case 'starting':
        return '启动中'
      case 'stopped':
        return '已停止'
      case 'failed':
        return '启动失败'
      default:
        return '未知'
    }
  }

  const canStart = currentInfo.status === 'stopped' || currentInfo.status === 'failed'
  const canStop = currentInfo.status === 'routing' || currentInfo.status === 'starting'

  // 获取按钮显示文本
  const getStartButtonText = () => {
    if (isOperating) return '发送中...'
    return '启动'
  }

  const getStopButtonText = () => {
    if (isOperating) return '发送中...'
    return '停止'
  }

  return (
    <div
      className={`model-card ${isSelected ? 'selected' : ''}`}
      onClick={onClick}
    >
      <div className="model-main">
        <div className="model-info">
          <div className="model-name">
            {currentInfo.model_name || currentInfo.aliases?.[0] || '未知模型'}
          </div>
          <div className="model-mode">{currentInfo.mode}</div>
        </div>

        <div className="model-controls">
          <div className={`model-status ${getStatusColor(currentInfo.status)}`}>
            {getStatusText(currentInfo.status)}
          </div>

          <div className="model-actions">
            {canStart && (
              <button
                className="action-btn start-btn"
                onClick={handleStart}
                disabled={isOperating}
                title="启动模型"
              >
                {getStartButtonText()}
              </button>
            )}

            {canStop && (
              <button
                className="action-btn stop-btn"
                onClick={handleStop}
                disabled={isOperating}
                title="停止模型"
              >
                {getStopButtonText()}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="model-details">
        <div className="model-stats">
          <span className="pending-requests">
            待处理: {currentInfo.pending_requests}
          </span>
          {currentInfo.pid && (
            <span className="pid-info">
              PID: {currentInfo.pid}
            </span>
          )}
        </div>

        {currentInfo.failure_reason && (
          <div className="error-message">
            {currentInfo.failure_reason}
          </div>
        )}

        {successMessage && (
          <div className="success-message">
            {successMessage}
          </div>
        )}

        {error && (
          <div className="action-error">
            {error}
          </div>
        )}
      </div>
    </div>
  )
}

export default ModelCard