import React, { useState, useEffect } from 'react'

interface TimeRangeSelectorProps {
  onTimeRangeChange: (startTime: Date, endTime: Date) => void
}

const TimeRangeSelector: React.FC<TimeRangeSelectorProps> = ({ onTimeRangeChange }) => {
  const [startDate, setStartDate] = useState<string>('')
  const [startHour, setStartHour] = useState<string>('00')
  const [startMinute, setStartMinute] = useState<string>('00')
  const [endDate, setEndDate] = useState<string>('')
  const [endHour, setEndHour] = useState<string>('00')
  const [endMinute, setEndMinute] = useState<string>('00')

  // 初始化默认时间（今天到明天）
  useEffect(() => {
    const today = new Date()
    const tomorrow = new Date(today)
    tomorrow.setDate(today.getDate() + 1)

    // 修复：使用本地时间而非 UTC 时间格式化
    // 之前的 toISOString() 会导致在东八区凌晨 0-8 点时日期显示为前一天
    const formatDate = (date: Date) => {
      const year = date.getFullYear()
      const month = String(date.getMonth() + 1).padStart(2, '0')
      const day = String(date.getDate()).padStart(2, '0')
      return `${year}-${month}-${day}`
    }

    setStartDate(formatDate(today))
    setEndDate(formatDate(tomorrow))
  }, [])

  // 生成小时选项（0-23）
  const generateHourOptions = () => {
    return Array.from({ length: 24 }, (_, i) => i.toString().padStart(2, '0'))
  }

  // 生成分钟选项（0-59）
  const generateMinuteOptions = () => {
    return Array.from({ length: 60 }, (_, i) => i.toString().padStart(2, '0'))
  }

  const handleTimeChange = () => {
    if (startDate && endDate) {
      const startDateTime = new Date(`${startDate}T${startHour}:${startMinute}:00`)
      const endDateTime = new Date(`${endDate}T${endHour}:${endMinute}:00`)

      // 确保开始时间早于结束时间
      if (startDateTime < endDateTime) {
        onTimeRangeChange(startDateTime, endDateTime)
      }
    }
  }

  // 监听任何时间输入的变化
  useEffect(() => {
    handleTimeChange()
  }, [startDate, startHour, startMinute, endDate, endHour, endMinute])

  return (
    <div className="time-range-selector">
      <div className="time-range-container">
        <div className="time-range-controls">
          {/* 左侧指示文字 */}
          <div className="time-range-label">时间区间选择</div>

          {/* 右侧时间选择 */}
          <div className="time-inputs-group">
            {/* 开始时间 */}
            <div className="time-inputs">
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="time-date-input"
              />
              <select
                value={startHour}
                onChange={(e) => setStartHour(e.target.value)}
                className="time-select"
              >
                {generateHourOptions().map(hour => (
                  <option key={hour} value={hour}>{hour}</option>
                ))}
              </select>
              <span className="time-separator">:</span>
              <select
                value={startMinute}
                onChange={(e) => setStartMinute(e.target.value)}
                className="time-select"
              >
                {generateMinuteOptions().map(minute => (
                  <option key={minute} value={minute}>{minute}</option>
                ))}
              </select>
            </div>

            {/* 分隔符 */}
            <div className="time-range-separator">至</div>

            {/* 结束时间 */}
            <div className="time-inputs">
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="time-date-input"
              />
              <select
                value={endHour}
                onChange={(e) => setEndHour(e.target.value)}
                className="time-select"
              >
                {generateHourOptions().map(hour => (
                  <option key={hour} value={hour}>{hour}</option>
                ))}
              </select>
              <span className="time-separator">:</span>
              <select
                value={endMinute}
                onChange={(e) => setEndMinute(e.target.value)}
                className="time-select"
              >
                {generateMinuteOptions().map(minute => (
                  <option key={minute} value={minute}>{minute}</option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default TimeRangeSelector