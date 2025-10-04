import React, { useState, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { apiService } from '../utils/api'
import { HealthResponse, ApiInfoResponse } from '../types/api'

const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const location = useLocation()
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [apiInfo, setApiInfo] = useState<ApiInfoResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [healthData, apiInfoData] = await Promise.all([
          apiService.getHealth(),
          apiService.getApiInfo()
        ])
        setHealth(healthData)
        setApiInfo(apiInfoData)
      } catch (error) {
        console.error('Failed to fetch data:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 30000) // 30秒刷新一次

    return () => clearInterval(interval)
  }, [])

  const navItems = [
    { path: '/', label: '总览' },
    { path: '/model-monitoring', label: '模型数据监控' },
    { path: '/billing-settings', label: '模型计费设置' },
    { path: '/data-management', label: '数据管理' }
  ]

  return (
    <div className="layout">
      <nav className="navbar">
        <div className="navbar-left">
          <div className="brand-logo">
            <h1 className="app-title">LLM Manager</h1>
            <span className="app-subtitle">控制台</span>
            {apiInfo && (
              <span className="app-version">v{apiInfo.version}</span>
            )}
          </div>
        </div>

        <div className="nav-center">
          <div className="navbar-nav">
            {navItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                className={`nav-item ${location.pathname === item.path ? 'active' : ''}`}
              >
                {item.label}
              </Link>
            ))}
          </div>
        </div>

        <div className="navbar-right">
          <div className="health-indicator">
            {loading ? (
              <div className="health-loading">
                <div className="loading-spinner"></div>
                <span>检查中</span>
              </div>
            ) : health ? (
              <div className={`health-status ${health.status}`}>
                <div className="health-icon">
                  {health.status === 'healthy' ? '✓' : '⚠'}
                </div>
                <span>
                  {health.status === 'healthy' ? '服务正常' : '服务异常'}
                </span>
              </div>
            ) : (
              <div className="health-error">
                <div className="health-icon">✕</div>
                <span>连接失败</span>
              </div>
            )}
          </div>
        </div>
      </nav>

      <main className="main-content">
        {children}
      </main>
    </div>
  )
}

export default Layout