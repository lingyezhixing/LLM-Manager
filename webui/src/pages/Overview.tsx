import React from 'react'
import DeviceMonitor from '../components/DeviceMonitor'
import ProgramStatusMonitor from '../components/ProgramStatusMonitor'

const Overview: React.FC = () => {
  return (
    <div className="overview-page">
      <DeviceMonitor />
      <ProgramStatusMonitor />
    </div>
  )
}

export default Overview