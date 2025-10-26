import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import { ModelPricingResponse, TierPricing } from '../types/api'

interface BillingPricingContentProps {
  selectedModel: string | null
}

const BillingPricingContent: React.FC<BillingPricingContentProps> = ({
  selectedModel
}) => {
  const [pricingData, setPricingData] = useState<ModelPricingResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (selectedModel) {
      fetchPricingData(selectedModel)
    }
  }, [selectedModel])

  const fetchPricingData = async (modelName: string) => {
    try {
      setLoading(true)
      setError(null)
      const data = await apiService.getModelPricing(modelName)
      setPricingData(data)
    } catch (err) {
      console.error('Failed to fetch pricing data:', err)
      setError('获取计费信息失败')
    } finally {
      setLoading(false)
    }
  }

  if (!selectedModel) {
    return (
      <div className="content-placeholder">
        <p>请选择一个模型查看计费配置</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="content-placeholder">
        <div className="loading-spinner"></div>
        <span>加载计费信息中...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="content-placeholder">
        <div className="error-icon">⚠</div>
        <span>{error}</span>
      </div>
    )
  }

  if (!pricingData) {
    return (
      <div className="content-placeholder">
        <p>暂无计费信息</p>
      </div>
    )
  }

  const { data } = pricingData

  return (
    <div className="billing-pricing-content">
      <div className="pricing-header">
        <h2 className="pricing-title">{data.model_name} - 计费配置</h2>
        <div className="pricing-type-badge">
          {data.pricing_type === 'tier' ? '按量计费' : '按时计费'}
        </div>
      </div>

      <div className="pricing-content">
        {data.pricing_type === 'tier' && data.tier_pricing && (
          <div className="tier-pricing-section">
            <h3 className="section-title">分阶按量计费</h3>
            <div className="tier-cards">
              {data.tier_pricing.map((tier: TierPricing) => (
                <div key={tier.tier_index} className="tier-card">
                  <div className="tier-header">
                    <span className="tier-index">档位 {tier.tier_index}</span>
                  </div>
                  <div className="tier-content">
                    <div className="tier-ranges">
                      <div className="range-item">
                        <span className="range-label">输入Token范围:</span>
                        <span className="range-value">
                          {tier.min_input_tokens === 0 ? '0' : `>${tier.min_input_tokens}`}
                          {tier.max_input_tokens === -1 ? '+' : `-${tier.max_input_tokens}`}
                        </span>
                      </div>
                      <div className="range-item">
                        <span className="range-label">输出Token范围:</span>
                        <span className="range-value">
                          {tier.min_output_tokens === 0 ? '0' : `>${tier.min_output_tokens}`}
                          {tier.max_output_tokens === -1 ? '+' : `-${tier.max_output_tokens}`}
                        </span>
                      </div>
                    </div>
                    <div className="tier-prices">
                      <div className="price-item">
                        <span className="price-label">输入价格:</span>
                        <span className="price-value">{tier.input_price} 元/百万Token</span>
                      </div>
                      <div className="price-item">
                        <span className="price-label">输出价格:</span>
                        <span className="price-value">{tier.output_price} 元/百万Token</span>
                      </div>
                      {tier.support_cache && (
                        <div className="cache-prices">
                          <div className="price-item">
                            <span className="price-label">缓存写入:</span>
                            <span className="price-value">{tier.cache_write_price} 元/百万Token</span>
                          </div>
                          <div className="price-item">
                            <span className="price-label">缓存读取:</span>
                            <span className="price-value">{tier.cache_read_price} 元/百万Token</span>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {data.pricing_type === 'hourly' && data.hourly_price !== undefined && (
          <div className="hourly-pricing-section">
            <h3 className="section-title">按时计费</h3>
            <div className="hourly-price-card">
              <div className="price-display">
                <span className="price-amount">{data.hourly_price}</span>
                <span className="price-unit">元/小时</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default BillingPricingContent