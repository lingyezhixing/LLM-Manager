import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import { OpenAIModel } from '../types/api'
import BillingModelList from '../components/BillingModelList'
import BillingPricingContent from '../components/BillingPricingContent'
import '../styles/ModelMonitoring.css' // 复用模型监控页面的样式
import '../styles/BillingSettings.css' // 计费设置页面样式

const BillingSettings: React.FC = () => {
  const [models, setModels] = useState<OpenAIModel[]>([])
  const [selectedModel, setSelectedModel] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchModels()
  }, [])

  const fetchModels = async () => {
    try {
      setLoading(true)
      const data = await apiService.getModels()
      setModels(data.data)

      // 默认选择第一个模型
      if (data.data.length > 0) {
        setSelectedModel(data.data[0].id)
      }

      setError(null)
    } catch (err) {
      console.error('Failed to fetch models:', err)
      setError('获取模型列表失败')
    } finally {
      setLoading(false)
    }
  }

  const handleModelSelect = (modelName: string) => {
    setSelectedModel(modelName)
  }

  return (
    <div className="model-monitoring-page">
      <div className="model-monitoring-layout">
        {/* 左侧模型列表 */}
        <div className="model-list-section">
          <BillingModelList
            models={models}
            selectedModel={selectedModel}
            onModelSelect={handleModelSelect}
            loading={loading}
            error={error}
          />
        </div>

        {/* 右侧计费信息区域 */}
        <div className="model-content-section">
          <div className="main-content-area">
            <BillingPricingContent selectedModel={selectedModel} />
          </div>
        </div>
      </div>
    </div>
  )
}

export default BillingSettings