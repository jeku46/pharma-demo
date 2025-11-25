import { useState, useEffect, useRef } from 'react'
import { io } from 'socket.io-client'
import SpatialEditor from './SpatialEditor'
import Insights from './Insights'
import './App.css'

const BACKEND_URL = 'http://localhost:5001'

function App() {
  // Mode: 'live', 'zones', or 'insights'
  const [mode, setMode] = useState('live')

  // Upload mode state

  const [detectionResult, setDetectionResult] = useState(null)
  const [error, setError] = useState(null)

  // Live detection state
  const [cameraActive, setCameraActive] = useState(false)
  const [liveDetection, setLiveDetection] = useState(null)
  const [confidence, setConfidence] = useState(0.7)
  const [zones, setZones] = useState([])
  const [occupiedZoneIds, setOccupiedZoneIds] = useState([])
  const [nonCompliantZoneIds, setNonCompliantZoneIds] = useState([])
  const [ibcStatus, setIbcStatus] = useState({})
  const socketRef = useRef(null)
  const liveCanvasRef = useRef(null)

  


  // Fetch zones when in live mode
  useEffect(() => {
    if (mode === 'live') {
      fetchZones()
    }
  }, [mode])

  const fetchZones = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/zones`)
      const data = await response.json()
      console.log('Fetched zones:', data.zones)
      setZones(data.zones || [])
    } catch (err) {
      console.error('Failed to fetch zones:', err)
    }
  }

  // Draw zones on canvas
  useEffect(() => {
    if (mode === 'live' && liveCanvasRef.current && cameraActive && zones.length > 0) {
      const canvas = liveCanvasRef.current
      const ctx = canvas.getContext('2d')

      console.log('Drawing zones on canvas:', zones.length)

      const drawZones = () => {
        ctx.clearRect(0, 0, canvas.width, canvas.height)

        zones.forEach(zone => {
          const isOccupied = occupiedZoneIds.includes(zone._id)
          const isNonCompliant = nonCompliantZoneIds.includes(zone._id)

          // Fill - red if non-compliant, green if occupied & compliant, blue if empty
          let fillColor, strokeColor, labelBgColor
          if (isNonCompliant) {
            fillColor = 'rgba(244, 67, 54, 0.3)'  // Red
            strokeColor = '#f44336'
            labelBgColor = 'rgba(244, 67, 54, 0.9)'
          } else if (isOccupied) {
            fillColor = 'rgba(76, 175, 80, 0.3)'  // Green
            strokeColor = '#4caf50'
            labelBgColor = 'rgba(76, 175, 80, 0.9)'
          } else {
            fillColor = 'rgba(66, 133, 244, 0.2)'  // Blue
            strokeColor = '#4285f4'
            labelBgColor = 'rgba(0, 0, 0, 0.7)'
          }

          ctx.fillStyle = fillColor
          ctx.fillRect(zone.x, zone.y, zone.width, zone.height)

          // Border
          ctx.strokeStyle = strokeColor
          ctx.lineWidth = (isOccupied || isNonCompliant) ? 3 : 2
          ctx.strokeRect(zone.x, zone.y, zone.width, zone.height)

          // Label
          ctx.fillStyle = '#fff'
          ctx.font = 'bold 14px Arial'
          const textWidth = ctx.measureText(zone.name).width
          ctx.fillStyle = labelBgColor
          ctx.fillRect(zone.x, zone.y - 22, textWidth + 10, 22)
          ctx.fillStyle = '#fff'
          ctx.fillText(zone.name, zone.x + 5, zone.y - 6)
        })
      }

      // Draw immediately
      drawZones()

      // Redraw zones every 100ms to keep them visible
      const interval = setInterval(drawZones, 100)
      return () => clearInterval(interval)
    }
  }, [mode, cameraActive, zones, occupiedZoneIds, nonCompliantZoneIds])

  // WebSocket connection for live detection
  useEffect(() => {
    if (mode === 'live') {
      socketRef.current = io(BACKEND_URL)

      socketRef.current.on('connected', (data) => {
        console.log('WebSocket connected:', data)
      })

      socketRef.current.on('detection', (data) => {
        console.log('Detection event:', data)
        setLiveDetection(data)
      })

      socketRef.current.on('zone_occupancy', (data) => {
        console.log('Zone occupancy:', data.occupied_zone_ids, 'Non-compliant:', data.non_compliant_zone_ids)
        setOccupiedZoneIds(data.occupied_zone_ids || [])
        setNonCompliantZoneIds(data.non_compliant_zone_ids || [])
        setIbcStatus(data.ibc_status || {})
      })

      return () => {
        if (socketRef.current) {
          socketRef.current.disconnect()
        }
        setOccupiedZoneIds([])
        setNonCompliantZoneIds([])
      }
    }
  }, [mode])

  const startCamera = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/start_camera`, {
        method: 'POST'
      })
      const data = await response.json()
      if (data.status === 'started' || data.status === 'already_running') {
        setCameraActive(true)
        setLiveDetection(null)
      }
    } catch (err) {
      console.error('Failed to start camera:', err)
      setError('Failed to start camera')
    }
  }

  const stopCamera = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/stop_camera`, {
        method: 'POST'
      })
      const data = await response.json()
      if (data.status === 'stopped' || data.status === 'not_running') {
        setCameraActive(false)
        setLiveDetection(null)
      }
    } catch (err) {
      console.error('Failed to stop camera:', err)
      setError('Failed to stop camera')
    }
  }

  const updateConfidence = async (newConfidence) => {
    setConfidence(newConfidence)
    try {
      await fetch(`${BACKEND_URL}/set_confidence`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confidence: newConfidence })
      })
    } catch (err) {
      console.error('Failed to update confidence:', err)
    }
  }

  const switchMode = (newMode) => {
    // Only stop camera when switching to upload mode
    if (newMode === 'upload' && cameraActive) {
      stopCamera()
    }
    setMode(newMode)
    setError(null)
    setDetectionResult(null)
    setLiveDetection(null)
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>GMP Wash Cycle Compliance</h1>
        <p className="subtitle">
          {mode === 'live' ? 'Live IBC monitoring and zone tracking' :
           mode === 'zones' ? 'Define spatial zones for detection areas' :
           'Analytics and compliance monitoring'}
        </p>

        <div className="mode-switcher">
          <button
            className={`mode-button ${mode === 'live' ? 'active' : ''}`}
            onClick={() => switchMode('live')}
          >
            Live Detection
          </button>
          <button
            className={`mode-button ${mode === 'zones' ? 'active' : ''}`}
            onClick={() => switchMode('zones')}
          >
            Zone Editor
          </button>
          <button
            className={`mode-button ${mode === 'insights' ? 'active' : ''}`}
            onClick={() => switchMode('insights')}
          >
            Insights
          </button>
        </div>
      </header>

      <main className="app-main">
        {mode === 'live' ? (
          // Live Detection Mode
          <div className="live-container">
            <div className="video-wrapper" style={{ position: 'relative' }}>
              {cameraActive ? (
                <>
                  <img
                    src={`${BACKEND_URL}/video_feed`}
                    alt="Live video feed"
                    className="live-video"
                  />
                  <canvas
                    ref={liveCanvasRef}
                    className="zone-overlay"
                    width={640}
                    height={480}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      height: '100%',
                      pointerEvents: 'none',
                      zIndex: 10
                    }}
                  />
                </>
              ) : (
                <div className="video-placeholder">
                  <svg
                    className="camera-icon"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"
                    />
                  </svg>
                  <p>Camera is off</p>
                </div>
              )}
            </div>

            {cameraActive && Object.keys(ibcStatus).length > 0 && (
              <div className="ibc-status-list" style={{
                marginTop: '20px',
                padding: '16px',
                background: 'rgba(255, 255, 255, 0.05)',
                borderRadius: '8px',
                border: '1px solid rgba(255, 255, 255, 0.1)'
              }}>
                <h3 style={{
                  margin: '0 0 12px 0',
                  fontSize: '16px',
                  fontWeight: '600',
                  color: '#e0e0e0'
                }}>Detected IBCs</h3>
                <div style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '8px'
                }}>
                  {Object.entries(ibcStatus).map(([ibcId, className]) => (
                    <div key={ibcId} style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      padding: '8px 12px',
                      background: 'rgba(255, 255, 255, 0.03)',
                      borderRadius: '4px',
                      border: '1px solid rgba(255, 255, 255, 0.1)'
                    }}>
                      <span style={{
                        fontWeight: '600',
                        color: '#4285f4'
                      }}>IBC-{ibcId}</span>
                      <span style={{
                        color: className.toLowerCase().endsWith('empty') ? '#34a853' : '#fbbc04'
                      }}>{className}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="action-buttons">
              {!cameraActive ? (
                <button className="detect-button" onClick={startCamera}>
                  Start Camera
                </button>
              ) : (
                <button className="reset-button" onClick={stopCamera}>
                  Stop Camera
                </button>
              )}
            </div>

            <div className="confidence-slider">
              <label htmlFor="confidence">
                Confidence: {(confidence * 100).toFixed(0)}%
              </label>
              <input
                type="range"
                id="confidence"
                min="0"
                max="100"
                value={confidence * 100}
                onChange={(e) => updateConfidence(e.target.value / 100)}
              />
            </div>

            {error && (
              <div className="error-message">
                {error}
              </div>
            )}
          </div>
        ) : mode === 'zones' ? (
          // Zone Editor Mode
          <div className="zones-container">
            <SpatialEditor
              videoSrc={`${BACKEND_URL}/video_feed`}
              isActive={cameraActive}
            />

            <div className="action-buttons">
              {!cameraActive ? (
                <button className="detect-button" onClick={startCamera}>
                  Start Camera
                </button>
              ) : (
                <button className="reset-button" onClick={stopCamera}>
                  Stop Camera
                </button>
              )}
            </div>
          </div>
        ) : (
          // Insights Mode
          <Insights />
        )}
      </main>
    </div>
  )
}

export default App
