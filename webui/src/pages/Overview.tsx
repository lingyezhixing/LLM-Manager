import React from 'react'
import DeviceMonitor from '../components/DeviceMonitor'
import ProgramStatusMonitor from '../components/ProgramStatusMonitor'
import ModelManager from '../components/ModelManager'

const Overview: React.FC = () => {
  return (
    <div className="overview-page">
      <DeviceMonitor />
      <ProgramStatusMonitor />
      <ModelManager />
    </div>
  )
}

export default Overview