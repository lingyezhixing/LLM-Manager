import React, { useState, useEffect, useRef } from 'react'
import { apiService } from '../utils/api'
import { ModelInfo, ModelsResponse } from '../types/api'
import ModelCard from './ModelCard'
import LogConsole from './LogConsole'

const ModelManager: React.FC = () => {
  const [modelsData, setModelsData] = useState<ModelsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedModel, setSelectedModel] = useState<string | null>(null)
  const refreshTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  // 获取模型数据
  const fetchModels = async () => {
    try {
      const response = await apiService.getModelsInfo()
      setModelsData(response)
      setError(null)
    } catch (err) {
      console.error('Failed to fetch models info:', err)
      // Don't set error for intermittent failures, just log it
      // setError('获取模型信息失败')
    } finally {
      setLoading(false)
    }
  }

  // 刷新模型数据的逻辑
  const startRefreshCycle = () => {
    refreshTimeoutRef.current = setTimeout(() => {
      fetchModels().finally(() => {
        // Only start next cycle if component is still mounted
        if (refreshTimeoutRef.current) {
          startRefreshCycle()
        }
      })
    }, 3000) // 每3秒刷新一次
  }

  useEffect(() => {
    // 首次获取数据
    fetchModels().finally(() => {
      startRefreshCycle()
    })

    // 清理函数
    return () => {
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current)
      }
    }
  }, [])

  // 处理模型卡片点击
  const handleModelClick = (modelName: string) => {
    setSelectedModel(modelName)
  }

  // 处理模型状态变化（用于通知子组件刷新）
  const handleModelStatusChange = () => {
    // ModelManager统一管理刷新，暂时不需要额外处理
  }

  // 获取模型列表（按字母顺序排序）
  const getModelEntries = () => {
    if (!modelsData || !modelsData.success) {
      return []
    }

    return Object.entries(modelsData.models)
      .sort(([a], [b]) => a.localeCompare(b))
  }

  const modelEntries = getModelEntries()

  if (loading) {
    return (
      <div className="model-manager">
        <div className="model-manager-header">
          <h2 className="model-manager-title">模型管理</h2>
        </div>
        <div className="model-manager-content">
          <div className="model-list-container">
            <div className="model-list-header">
              <h3 className="model-list-title">模型列表</h3>
            </div>
            <div className="model-list loading">
              <div className="loading-spinner"></div>
              <span>加载模型信息中...</span>
            </div>
          </div>
          <div className="log-console-container">
            <div className="log-console-header">
              <h3 className="log-console-title">模型控制台</h3>
            </div>
            <div className="no-model-selected">
              <div className="no-model-icon">📋</div>
              <div>请选择一个模型查看日志</div>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="model-manager">
        <div className="model-manager-header">
          <h2 className="model-manager-title">模型管理</h2>
        </div>
        <div className="model-manager-content">
          <div className="model-list-container">
            <div className="model-list-header">
              <h3 className="model-list-title">模型列表</h3>
            </div>
            <div className="model-list error">
              <div>⚠</div>
              <div>{error}</div>
            </div>
          </div>
          <div className="log-console-container">
            <div className="log-console-header">
              <h3 className="log-console-title">模型控制台</h3>
            </div>
            <div className="no-model-selected">
              <div className="no-model-icon">❌</div>
              <div>加载失败，请刷新页面重试</div>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (!modelsData || !modelsData.success) {
    return (
      <div className="model-manager">
        <div className="model-manager-header">
          <h2 className="model-manager-title">模型管理</h2>
        </div>
        <div className="model-manager-content">
          <div className="model-list-container">
            <div className="model-list-header">
              <h3 className="model-list-title">模型列表</h3>
            </div>
            <div className="model-list error">
              <div>⚠</div>
              <div>模型数据不可用</div>
            </div>
          </div>
          <div className="log-console-container">
            <div className="log-console-header">
              <h3 className="log-console-title">模型控制台</h3>
            </div>
            <div className="no-model-selected">
              <div className="no-model-icon">❌</div>
              <div>模型数据不可用</div>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="model-manager">
      <div className="model-manager-header">
        <h2 className="model-manager-title">
          模型管理
          <span style={{
            fontSize: '0.75rem',
            color: '#6b7280',
            marginLeft: '0.5rem',
            fontWeight: 400
          }}>
            总计: {modelsData.total_models} | 运行中: {modelsData.running_models} | 待处理: {modelsData.total_pending_requests}
          </span>
        </h2>
      </div>

      <div className="model-manager-content">
        {/* 左侧模型列表 (1/4) */}
        <div className="model-list-container">
          <div className="model-list-header">
            <h3 className="model-list-title">模型列表</h3>
          </div>
          <div className="model-list">
            {modelEntries.length === 0 ? (
              <div className="no-model-selected">
                <div>未发现可用模型</div>
              </div>
            ) : (
              modelEntries.map(([modelName, modelInfo]) => (
                <ModelCard
                  key={modelName}
                  modelInfo={modelInfo}
                  isSelected={selectedModel === modelName}
                  onClick={() => handleModelClick(modelName)}
                  onStatusChange={handleModelStatusChange}
                />
              ))
            )}
          </div>
        </div>

        {/* 右侧日志控制台 (3/4) */}
        <div className="log-console-container">
          <LogConsole selectedModel={selectedModel} />
        </div>
      </div>
    </div>
  )
}

export default ModelManager