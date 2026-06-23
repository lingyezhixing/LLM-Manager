import React from 'react'
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Overview from './pages/Overview'
import ModelMonitoring from './pages/ModelMonitoring'
import BillingSettings from './pages/BillingSettings'
import DataManagement from './pages/DataManagement'

function App() {
  return (
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/model-monitoring" element={<ModelMonitoring />} />
          <Route path="/billing-settings" element={<BillingSettings />} />
          <Route path="/data-management" element={<DataManagement />} />
        </Routes>
      </Layout>
    </Router>
  )
}

export default App