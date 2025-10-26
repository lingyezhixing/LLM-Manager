import React from 'react'
import { OpenAIModel } from '../types/api'

interface BillingModelListProps {
  models: OpenAIModel[]
  selectedModel: string | null
  onModelSelect: (modelName: string) => void
  loading: boolean
  error: string | null
}

const BillingModelList: React.FC<BillingModelListProps> = ({
  models,
  selectedModel,
  onModelSelect,
  loading,
  error
}) => {
  const handleCardClick = (modelName: string) => {
    onModelSelect(modelName)
  }

  if (loading) {
    return (
      <div className="model-list-card loading">
        <div className="loading-spinner"></div>
        <span>加载模型列表...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="model-list-card error">
        <div className="error-icon">⚠</div>
        <span>{error}</span>
      </div>
    )
  }

  return (
    <div className="model-list-card">
      <div className="model-list-header">
        <h3 className="model-list-title">模型列表</h3>
      </div>

      <div className="model-list-content">
        {/* 模型卡片列表 */}
        {models.map((model) => (
          <div
            key={model.id}
            className={`model-list-item ${selectedModel === model.id ? 'selected' : ''}`}
            onClick={() => handleCardClick(model.id)}
          >
            <div className="model-item-content">
              <span className="model-name">{model.id}</span>
              <span className="model-mode">{model.mode}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default BillingModelList