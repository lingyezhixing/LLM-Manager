import React, { useState, useEffect } from 'react'
import { apiService } from '../utils/api'
import { StorageStatsResponse, OrphanedModelsResponse, DeleteModelDataResponse } from '../types/api'

const DataManagement: React.FC = () => {
  const [storageStats, setStorageStats] = useState<StorageStatsResponse | null>(null)
  const [orphanedModels, setOrphanedModels] = useState<OrphanedModelsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deleteLoading, setDeleteLoading] = useState<string | null>(null)

  const fetchData = async () => {
    try {
      setLoading(true)
      setError(null)
      const [storageData, orphanedData] = await Promise.all([
        apiService.getStorageStats(),
        apiService.getOrphanedModels()
      ])
      setStorageStats(storageData)
      setOrphanedModels(orphanedData)
    } catch (error) {
      console.error('Failed to fetch data management data:', error)
      setError('获取数据管理信息失败')
    } finally {
      setLoading(false)
    }
  }

  const handleDeleteModelData = async (modelName: string) => {
    if (!window.confirm(`确定要删除模型 "${modelName}" 的所有数据吗？此操作不可撤销。`)) {
      return
    }

    try {
      setDeleteLoading(modelName)
      const response = await apiService.deleteModelData(modelName)
      if (response.success) {
        // 重新获取数据
        await fetchData()
      } else {
        setError('删除模型数据失败')
      }
    } catch (error) {
      console.error('Failed to delete model data:', error)
      setError('删除模型数据失败')
    } finally {
      setDeleteLoading(null)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  if (loading) {
    return (
      <div className="loading">
        <div className="loading-spinner"></div>
        <span>加载数据中...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="error">
        <div className="error-icon">⚠</div>
        <div>
          <div>{error}</div>
          <button
            className="action-btn start-btn"
            onClick={fetchData}
            style={{ marginTop: '1rem' }}
          >
            重试
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="data-management-page">
      <div className="page-header">
        <h1 className="page-title">数据管理</h1>
        <p className="page-description">管理数据库存储和孤立模型数据</p>
      </div>

      {/* 存储统计信息 */}
      <div className="storage-stats-section">
        <div className="section-header">
          <h2 className="section-title">存储统计</h2>
        </div>
        {storageStats && (
          <div className="stats-grid">
            <div className="stat-card">
              <div className="stat-label">数据库状态</div>
              <div className="stat-value">
                <span className={`status-indicator ${storageStats.data.database_exists ? 'online' : 'offline'}`}>
                  {storageStats.data.database_exists ? '存在' : '不存在'}
                </span>
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-label">数据库大小</div>
              <div className="stat-value">{storageStats.data.database_size_mb.toFixed(2)} MB</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">有数据的模型数</div>
              <div className="stat-value">{storageStats.data.total_models_with_data}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">总请求数</div>
              <div className="stat-value">{storageStats.data.total_requests}</div>
            </div>
          </div>
        )}
      </div>

      {/* 孤立模型管理 */}
      <div className="orphaned-models-section">
        <div className="section-header">
          <h2 className="section-title">孤立模型管理</h2>
          <button className="refresh-btn" onClick={fetchData}>
            刷新
          </button>
        </div>

        {orphanedModels && orphanedModels.data.count > 0 ? (
          <div className="orphaned-models-list">
            <div className="list-header">
              <span>发现 {orphanedModels.data.count} 个孤立模型</span>
            </div>
            {orphanedModels.data.orphaned_models.map(modelName => (
              <div key={modelName} className="orphaned-model-item">
                <div className="model-info">
                  <div className="model-name">{modelName}</div>
                  <div className="model-description">此模型不在当前配置中，但数据库中存在相关数据</div>
                </div>
                <div className="model-actions">
                  <button
                    className="action-btn stop-btn"
                    onClick={() => handleDeleteModelData(modelName)}
                    disabled={deleteLoading === modelName}
                  >
                    {deleteLoading === modelName ? '删除中...' : '删除数据'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="no-orphaned-models">
            <div className="no-data-icon">✓</div>
            <div className="no-data-text">没有发现孤立模型</div>
            <div className="no-data-description">所有数据库中的模型都在当前配置中</div>
          </div>
        )}
      </div>

      {/* 模型数据详情 */}
      {storageStats && Object.keys(storageStats.data.models_data).length > 0 && (
        <div className="models-data-section">
          <div className="section-header">
            <h2 className="section-title">模型数据详情</h2>
          </div>
          <div className="models-data-table">
            <div className="table-header">
              <div className="header-cell model-name-cell">模型名称</div>
              <div className="header-cell request-count-cell">请求数量</div>
              <div className="header-cell runtime-data-cell">运行数据</div>
              <div className="header-cell billing-data-cell">计费数据</div>
            </div>
            <div className="table-body">
              {Object.entries(storageStats.data.models_data).map(([modelName, stats]) => (
                <div key={modelName} className="table-row">
                  <div className="table-cell model-name-cell">
                    <span className="model-name-text">{modelName}</span>
                    {stats.error && (
                      <div className="error-indicator" title={stats.error}>⚠</div>
                    )}
                  </div>
                  <div className="table-cell request-count-cell">
                    <span className="request-count">{stats.request_count}</span>
                  </div>
                  <div className="table-cell runtime-data-cell">
                    <span className={`data-status ${stats.has_runtime_data ? 'has-data' : 'no-data'}`}>
                      {stats.has_runtime_data ? '✓ 有' : '✗ 无'}
                    </span>
                  </div>
                  <div className="table-cell billing-data-cell">
                    <span className={`data-status ${stats.has_billing_data ? 'has-data' : 'no-data'}`}>
                      {stats.has_billing_data ? '✓ 有' : '✗ 无'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default DataManagement