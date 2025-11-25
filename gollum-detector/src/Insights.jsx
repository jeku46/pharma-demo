import { useState, useEffect } from 'react'
import './Insights.css'

const BACKEND_URL = 'http://localhost:5001'

function Insights() {
  const [dwellTimes, setDwellTimes] = useState([])
  const [occupancyIntervals, setOccupancyIntervals] = useState([])
  const [washSchedule, setWashSchedule] = useState([])
  const [complianceEvents, setComplianceEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchAllData()
    // Refresh data every 30 seconds
    const interval = setInterval(fetchAllData, 30000)
    return () => clearInterval(interval)
  }, [])

  const fetchAllData = async () => {
    try {
      setLoading(true)
      const [dwellResponse, occupancyResponse, washResponse, eventsResponse] = await Promise.all([
        fetch(`${BACKEND_URL}/insights/dwell-time`),
        fetch(`${BACKEND_URL}/occupancy?limit=100`),
        fetch(`${BACKEND_URL}/insights/wash-schedule`),
        fetch(`${BACKEND_URL}/insights/compliance-events`)
      ])

      const dwellData = await dwellResponse.json()
      const occupancyData = await occupancyResponse.json()
      const washData = await washResponse.json()
      const eventsData = await eventsResponse.json()

      setDwellTimes(dwellData.dwell_times || [])
      setOccupancyIntervals(occupancyData.intervals || [])
      setWashSchedule(washData.wash_schedule || [])
      setComplianceEvents(eventsData.events || [])
      setError(null)
    } catch (err) {
      console.error('Error fetching insights:', err)
      setError('Failed to load insights data')
    } finally {
      setLoading(false)
    }
  }

  const clearDatabase = async () => {
    if (!window.confirm('Are you sure you want to clear all data? This cannot be undone.')) {
      return
    }

    try {
      const response = await fetch(`${BACKEND_URL}/clear_database`, {
        method: 'POST'
      })

      const data = await response.json()

      if (data.status === 'cleared') {
        alert(`Database cleared successfully!\n${data.occupancy_records_deleted} occupancy records and ${data.ibc_records_deleted} IBC records deleted.`)
        fetchAllData() // Refresh to show empty data
      } else {
        alert(`Error: ${data.message}`)
      }
    } catch (err) {
      console.error('Error clearing database:', err)
      alert('Failed to clear database')
    }
  }

  const formatTimestamp = (timestamp) => {
    return new Date(timestamp * 1000).toLocaleString()
  }

  const formatDuration = (seconds) => {
    if (!seconds) return 'N/A'
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    const secs = Math.floor(seconds % 60)

    if (hours > 0) {
      return `${hours}h ${minutes}m ${secs}s`
    } else if (minutes > 0) {
      return `${minutes}m ${secs}s`
    } else {
      return `${secs}s`
    }
  }

  if (loading && dwellTimes.length === 0) {
    return <div className="insights-loading">Loading insights...</div>
  }

  return (
    <div className="insights-container">
      {error && <div className="insights-error">{error}</div>}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '20px' }}>
        <button
          onClick={clearDatabase}
          style={{
            padding: '10px 20px',
            backgroundColor: '#f44336',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '14px',
            fontWeight: '600'
          }}
        >
          Clear All Data
        </button>
      </div>

      {/* Dwell Time Section */}
      <section className="insights-section">
        <h2 className="insights-section-title">Zone Dwell Time Analytics</h2>
        {dwellTimes.length === 0 ? (
          <p className="insights-empty">No dwell time data available yet.</p>
        ) : (
          <div className="dwell-time-grid">
            {dwellTimes.map((zone, index) => (
              <div key={index} className="dwell-time-card">
                <h3 className="zone-name">{zone.zone_name}</h3>
                <div className="stat-grid">
                  <div className="stat">
                    <span className="stat-label">Total Visits</span>
                    <span className="stat-value">{zone.count}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Avg Time</span>
                    <span className="stat-value">{zone.avg_time_minutes.toFixed(1)} min</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Min Time</span>
                    <span className="stat-value">{zone.min_time_minutes.toFixed(1)} min</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Max Time</span>
                    <span className="stat-value">{zone.max_time_minutes.toFixed(1)} min</span>
                  </div>
                  <div className="stat stat-total">
                    <span className="stat-label">Total Hours</span>
                    <span className="stat-value">{zone.total_time_hours.toFixed(2)} hrs</span>
                  </div>
                </div>
                {/* Simple bar visualization */}
                <div className="dwell-bar-container">
                  <div
                    className="dwell-bar"
                    style={{width: `${Math.min(100, (zone.avg_time_minutes / Math.max(...dwellTimes.map(z => z.avg_time_minutes)) * 100))}%`}}
                  ></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Occupancy Intervals Section */}
      <section className="insights-section">
        <h2 className="insights-section-title">Occupancy Intervals History (Last 100)</h2>
        {occupancyIntervals.length === 0 ? (
          <p className="insights-empty">No occupancy data available yet.</p>
        ) : (
          <div className="occupancy-table">
            <table>
              <thead>
                <tr>
                  <th>IBC ID</th>
                  <th>Zone</th>
                  <th>Entered</th>
                  <th>Status on Entry</th>
                  <th>Exited</th>
                  <th>Status on Exit</th>
                  <th>Duration</th>
                </tr>
              </thead>
              <tbody>
                {occupancyIntervals.map((interval) => (
                  <tr key={interval._id} className={interval.exit_time ? '' : 'active-occupancy'}>
                    <td className="ibc-id">{interval.ibc_id}</td>
                    <td>{interval.zone_name}</td>
                    <td>{formatTimestamp(interval.enter_time)}</td>
                    <td>
                      <span className="fill-status-badge">
                        {interval.fill_status_on_entry ? interval.fill_status_on_entry.replace('IBC-', '') : 'Unknown'}
                      </span>
                    </td>
                    <td>{interval.exit_time ? formatTimestamp(interval.exit_time) : <span className="status-active">Still in zone</span>}</td>
                    <td>
                      {interval.fill_status_on_exit ? (
                        <span className="fill-status-badge">
                          {interval.fill_status_on_exit.replace('IBC-', '')}
                        </span>
                      ) : (
                        <span className="status-na">-</span>
                      )}
                    </td>
                    <td>{interval.duration ? formatDuration(interval.duration) : <span className="status-na">-</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Wash Schedule Section */}
      <section className="insights-section">
        <h2 className="insights-section-title">IBCs Requiring Wash</h2>
        {washSchedule.length === 0 ? (
          <p className="insights-empty">All IBCs are compliant with wash schedule.</p>
        ) : (
          <div className="wash-schedule-table">
            <table>
              <thead>
                <tr>
                  <th>IBC ID</th>
                  <th>Days Since Wash</th>
                  <th>Last Cleaned</th>
                  <th>Urgency</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {washSchedule.map((ibc) => (
                  <tr key={ibc.ibc_id} className={`urgency-${ibc.urgency}`}>
                    <td className="ibc-id">IBC-{ibc.ibc_id}</td>
                    <td>{ibc.days_since_wash !== null ? `${ibc.days_since_wash} days` : 'Never'}</td>
                    <td>{ibc.last_cleaned ? formatTimestamp(ibc.last_cleaned) : 'Never washed'}</td>
                    <td>
                      <span className={`urgency-badge urgency-${ibc.urgency}`}>
                        {ibc.urgency}
                      </span>
                    </td>
                    <td>
                      <span className="status-badge">Needs Wash</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Compliance Events Section */}
      <section className="insights-section">
        <h2 className="insights-section-title">Compliance Violations (Last 7 Days)</h2>
        {complianceEvents.length === 0 ? (
          <p className="insights-empty">No compliance violations recorded.</p>
        ) : (
          <div className="compliance-events-list">
            {complianceEvents.map((event) => (
              <div key={event._id} className={`compliance-event severity-${event.severity}`}>
                <div className="event-header">
                  <span className={`severity-badge severity-${event.severity}`}>
                    {event.severity}
                  </span>
                  <span className="event-time">{formatTimestamp(event.timestamp)}</span>
                </div>
                <div className="event-details">
                  <div className="event-detail-row">
                    <strong>IBC:</strong> IBC-{event.ibc_id}
                  </div>
                  <div className="event-detail-row">
                    <strong>Zone:</strong> {event.zone_name}
                  </div>
                  <div className="event-detail-row">
                    <strong>Violation:</strong> {event.reason}
                  </div>
                  <div className="event-detail-row">
                    <strong>Last Cleaned:</strong> {event.last_cleaned ? formatTimestamp(event.last_cleaned) : 'Never'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

export default Insights
