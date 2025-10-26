import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import { ModelPricingResponse, TierPricing } from '../types/api'

interface BillingPricingContentProps {
  selectedModel: string | null
}

interface EditingTier {
  tier_index: number
  min_input_tokens: number
  max_input_tokens: number
  min_output_tokens: number
  max_output_tokens: number
  input_price: number
  output_price: number
  support_cache: boolean
  cache_write_price: number
  cache_read_price: number
}

const BillingPricingContent: React.FC<BillingPricingContentProps> = ({
  selectedModel
}) => {
  const [pricingData, setPricingData] = useState<ModelPricingResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pricingMethod, setPricingMethod] = useState<'tier' | 'hourly'>('tier')
  const [editingTiers, setEditingTiers] = useState<Record<number, EditingTier>>({})
  const [editingTierStates, setEditingTierStates] = useState<Record<number, boolean>>({})
  const [newTierIndex, setNewTierIndex] = useState<number>(1)
  const [hourlyPrice, setHourlyPrice] = useState<number>(0)
  const [editingHourly, setEditingHourly] = useState<boolean>(false)

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
      setPricingMethod(data.data.pricing_type)
      setHourlyPrice(data.data.hourly_price || 0)

      // Initialize editing tiers with current data
      if (data.data.tier_pricing) {
        const tiers: Record<number, EditingTier> = {}
        const editingStates: Record<number, boolean> = {}
        data.data.tier_pricing.forEach(tier => {
          tiers[tier.tier_index] = { ...tier }
          editingStates[tier.tier_index] = false
        })
        setEditingTiers(tiers)
        setEditingTierStates(editingStates)

        // 计算下一个新的阶梯索引
        const maxIndex = Math.max(...Object.keys(tiers).map(Number), 0)
        setNewTierIndex(maxIndex + 1)
      } else {
        // 如果没有阶梯数据，重置状态
        setEditingTiers({})
        setEditingTierStates({})
        setNewTierIndex(1)
      }
    } catch (err) {
      console.error('Failed to fetch pricing data:', err)
      setError('获取计费信息失败: ' + (err instanceof Error ? err.message : String(err)))
    } finally {
      setLoading(false)
    }
  }

  const handlePricingMethodChange = async (method: 'tier' | 'hourly') => {
    if (!selectedModel) return

    try {
      await apiService.setModelPricingMethod(selectedModel, method)
      setPricingMethod(method)
      await fetchPricingData(selectedModel)
    } catch (err) {
      console.error('Failed to change pricing method:', err)
      setError('切换计费方式失败')
    }
  }

  const handleTierEdit = (tierIndex: number) => {
    setEditingTierStates(prev => ({
      ...prev,
      [tierIndex]: true
    }))
  }

  const handleTierSave = async (tierIndex: number) => {
    if (!selectedModel) return

    try {
      const tierData = editingTiers[tierIndex]
      await apiService.setModelPricingTier(selectedModel, tierData)

      setEditingTierStates(prev => ({
        ...prev,
        [tierIndex]: false
      }))

      // 重新获取数据以确保状态同步
      await fetchPricingData(selectedModel)
    } catch (err) {
      console.error('Failed to save tier:', err)
      setError('保存阶梯计费失败: ' + (err instanceof Error ? err.message : String(err)))
    }
  }

  const handleTierDelete = async (tierIndex: number) => {
    if (!selectedModel) return

    // 不允许删除最后一个阶梯
    if (sortedTiers.length <= 1) {
      setError('不能删除最后一个阶梯')
      return
    }

    try {
      await apiService.deleteModelPricingTier(selectedModel, tierIndex)
      await fetchPricingData(selectedModel)
    } catch (err) {
      console.error('Failed to delete tier:', err)
      setError('删除阶梯计费失败')
    }
  }

  const handleAddTier = () => {
    if (!selectedModel) return

    // 获取当前最大的阶梯索引
    const currentTiers = Object.keys(editingTiers).map(Number)
    const maxIndex = currentTiers.length > 0 ? Math.max(...currentTiers) : 0
    const newTierIdx = maxIndex + 1

    // 获取最后一个阶梯的范围信息
    const lastTier = editingTiers[maxIndex]
    const lastMaxInput = lastTier ? lastTier.max_input_tokens : 0
    const lastMaxOutput = lastTier ? lastTier.max_output_tokens : 0

    const newTierData = {
      tier_index: newTierIdx,
      min_input_tokens: lastMaxInput === -1 ? 0 : lastMaxInput,
      max_input_tokens: -1,
      min_output_tokens: lastMaxOutput === -1 ? 0 : lastMaxOutput,
      max_output_tokens: -1,
      input_price: 0,
      output_price: 0,
      support_cache: false,
      cache_write_price: 0,
      cache_read_price: 0
    }

    setEditingTiers(prev => ({
      ...prev,
      [newTierIdx]: newTierData
    }))
    setEditingTierStates(prev => ({
      ...prev,
      [newTierIdx]: true // 新建阶梯自动进入编辑模式
    }))
    setNewTierIndex(newTierIdx + 1)
  }

  const handleHourlySave = async () => {
    if (!selectedModel) return

    try {
      await apiService.setModelPricingHourly(selectedModel, hourlyPrice)
      setEditingHourly(false)
      await fetchPricingData(selectedModel)
    } catch (err) {
      console.error('Failed to save hourly pricing:', err)
      setError('保存按时计费失败')
    }
  }

  const updateTierField = (tierIndex: number, field: keyof EditingTier, value: any) => {
    setEditingTiers(prev => ({
      ...prev,
      [tierIndex]: {
        ...prev[tierIndex],
        [field]: value
      }
    }))
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

  const sortedTiers = Object.keys(editingTiers)
    .map(Number)
    .sort((a, b) => a - b)

  return (
    <div className="billing-pricing-content">
      <div className="pricing-header">
        <h2 className="pricing-title">{pricingData.data.model_name} - 计费配置</h2>
      </div>

      <div className="pricing-method-selector">
        <div
          className={`pricing-method-card ${pricingMethod === 'tier' ? 'selected' : ''}`}
          onClick={() => handlePricingMethodChange('tier')}
        >
          <h3 className="method-title">按量阶梯计费</h3>
          <p className="method-description">根据Token使用量按阶梯计费</p>
        </div>
        <div
          className={`pricing-method-card ${pricingMethod === 'hourly' ? 'selected' : ''}`}
          onClick={() => handlePricingMethodChange('hourly')}
        >
          <h3 className="method-title">按时计费</h3>
          <p className="method-description">按使用时长计费</p>
        </div>
      </div>

      <div className="pricing-content">
        {pricingMethod === 'tier' && (
          <div className="tier-pricing-section">
            <div className="section-header">
              <h3 className="section-title">阶梯计费设置</h3>
              <button className="add-tier-btn" onClick={handleAddTier}>
                + 新建阶梯
              </button>
            </div>

            {sortedTiers.length === 0 ? (
              <div className="no-tiers">
                <p>暂无阶梯计费配置，请添加阶梯</p>
              </div>
            ) : (
              <div className="tier-table-container">
                <table className="tier-table">
                  <thead>
                    <tr>
                      <th>阶梯索引</th>
                      <th>最小输入Token</th>
                      <th>最大输入Token</th>
                      <th>最小输出Token</th>
                      <th>最大输出Token</th>
                      <th>输入Token价格</th>
                      <th>输出Token价格</th>
                      <th>支持缓存</th>
                      <th>缓存写入价格</th>
                      <th>缓存读取价格</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedTiers.map(tierIndex => {
                      const tier = editingTiers[tierIndex]
                      const isEditing = editingTierStates[tierIndex] || false

                      return (
                        <tr key={tierIndex} className="tier-row">
                          <td>{tierIndex}</td>
                          <td>
                            {isEditing ? (
                              <input
                                type="number"
                                value={tier.min_input_tokens}
                                onChange={(e) => updateTierField(tierIndex, 'min_input_tokens', parseInt(e.target.value) || 0)}
                                className="tier-input"
                              />
                            ) : (
                              tier.min_input_tokens
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <input
                                type="number"
                                value={tier.max_input_tokens === -1 ? '' : tier.max_input_tokens}
                                onChange={(e) => updateTierField(tierIndex, 'max_input_tokens', e.target.value === '' ? -1 : parseInt(e.target.value) || 0)}
                                placeholder="-1表示无上限"
                                className="tier-input"
                              />
                            ) : (
                              tier.max_input_tokens === -1 ? '无上限' : tier.max_input_tokens
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <input
                                type="number"
                                value={tier.min_output_tokens}
                                onChange={(e) => updateTierField(tierIndex, 'min_output_tokens', parseInt(e.target.value) || 0)}
                                className="tier-input"
                              />
                            ) : (
                              tier.min_output_tokens
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <input
                                type="number"
                                value={tier.max_output_tokens === -1 ? '' : tier.max_output_tokens}
                                onChange={(e) => updateTierField(tierIndex, 'max_output_tokens', e.target.value === '' ? -1 : parseInt(e.target.value) || 0)}
                                placeholder="-1表示无上限"
                                className="tier-input"
                              />
                            ) : (
                              tier.max_output_tokens === -1 ? '无上限' : tier.max_output_tokens
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <input
                                type="number"
                                step="0.001"
                                value={tier.input_price}
                                onChange={(e) => updateTierField(tierIndex, 'input_price', parseFloat(e.target.value) || 0)}
                                className="tier-input"
                              />
                            ) : (
                              tier.input_price.toFixed(3)
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <input
                                type="number"
                                step="0.001"
                                value={tier.output_price}
                                onChange={(e) => updateTierField(tierIndex, 'output_price', parseFloat(e.target.value) || 0)}
                                className="tier-input"
                              />
                            ) : (
                              tier.output_price.toFixed(3)
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <input
                                type="checkbox"
                                checked={tier.support_cache}
                                onChange={(e) => updateTierField(tierIndex, 'support_cache', e.target.checked)}
                                className="tier-checkbox"
                              />
                            ) : (
                              tier.support_cache ? '是' : '否'
                            )}
                          </td>
                          <td>
                            {isEditing && tier.support_cache ? (
                              <input
                                type="number"
                                step="0.001"
                                value={tier.cache_write_price}
                                onChange={(e) => updateTierField(tierIndex, 'cache_write_price', parseFloat(e.target.value) || 0)}
                                className="tier-input"
                              />
                            ) : (
                              tier.support_cache ? tier.cache_write_price.toFixed(3) : '-'
                            )}
                          </td>
                          <td>
                            {isEditing && tier.support_cache ? (
                              <input
                                type="number"
                                step="0.001"
                                value={tier.cache_read_price}
                                onChange={(e) => updateTierField(tierIndex, 'cache_read_price', parseFloat(e.target.value) || 0)}
                                className="tier-input"
                              />
                            ) : (
                              tier.support_cache ? tier.cache_read_price.toFixed(3) : '-'
                            )}
                          </td>
                          <td>
                            <div className="tier-actions">
                              {isEditing ? (
                                <button
                                  className="save-btn"
                                  onClick={() => handleTierSave(tierIndex)}
                                >
                                  保存
                                </button>
                              ) : (
                                <button
                                  className="edit-btn"
                                  onClick={() => handleTierEdit(tierIndex)}
                                >
                                  编辑
                                </button>
                              )}
                              <button
                                className="delete-btn"
                                onClick={() => handleTierDelete(tierIndex)}
                                disabled={sortedTiers.length <= 1}
                                title={sortedTiers.length <= 1 ? '不能删除最后一个阶梯' : '删除此阶梯'}
                              >
                                删除
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {pricingMethod === 'hourly' && (
          <div className="hourly-pricing-section">
            <h3 className="section-title">按时计费设置</h3>
            <div className="hourly-pricing-card">
              <div className="hourly-price-setting">
                <div className="price-input-group">
                  <label className="price-label">每小时价格（元）:</label>
                  <input
                    type="number"
                    step="0.001"
                    value={hourlyPrice}
                    onChange={(e) => setHourlyPrice(parseFloat(e.target.value) || 0)}
                    disabled={!editingHourly}
                    className="hourly-price-input"
                  />
                  <span className="price-unit">元/小时</span>
                </div>
                <div className="hourly-actions">
                  {editingHourly ? (
                    <button className="save-btn" onClick={handleHourlySave}>
                      保存
                    </button>
                  ) : (
                    <button className="edit-btn" onClick={() => setEditingHourly(true)}>
                      编辑
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default BillingPricingContent