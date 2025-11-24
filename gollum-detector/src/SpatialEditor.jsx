import { useState, useRef, useEffect } from 'react'

const BACKEND_URL = 'http://localhost:5001'

function SpatialEditor({ videoSrc, isActive }) {
  const [zones, setZones] = useState([])
  const [isDrawing, setIsDrawing] = useState(false)
  const [currentRect, setCurrentRect] = useState(null)
  const [startPoint, setStartPoint] = useState(null)
  const [editMode, setEditMode] = useState(false)
  const [showNameModal, setShowNameModal] = useState(false)
  const [pendingZone, setPendingZone] = useState(null)
  const [zoneName, setZoneName] = useState('')
  const [selectedZone, setSelectedZone] = useState(null)
  const canvasRef = useRef(null)
  const containerRef = useRef(null)

  // Load zones from backend
  useEffect(() => {
    fetchZones()
  }, [])

  const fetchZones = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/zones`)
      const data = await response.json()
      setZones(data.zones || [])
    } catch (err) {
      console.error('Failed to fetch zones:', err)
    }
  }

  const saveZone = async (zone) => {
    try {
      const response = await fetch(`${BACKEND_URL}/zones`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(zone)
      })
      const data = await response.json()
      if (data.status === 'created') {
        setZones(prev => [...prev, data.zone])
      }
    } catch (err) {
      console.error('Failed to save zone:', err)
    }
  }

  const deleteZone = async (zoneId) => {
    try {
      const response = await fetch(`${BACKEND_URL}/zones/${zoneId}`, {
        method: 'DELETE'
      })
      const data = await response.json()
      if (data.status === 'deleted') {
        setZones(prev => prev.filter(z => z._id !== zoneId))
        setSelectedZone(null)
      }
    } catch (err) {
      console.error('Failed to delete zone:', err)
    }
  }

  const getCanvasCoordinates = (e) => {
    const canvas = canvasRef.current
    const rect = canvas.getBoundingClientRect()
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top
    }
  }

  const handleMouseDown = (e) => {
    if (!editMode) return

    const coords = getCanvasCoordinates(e)

    // Check if clicking on existing zone
    const clickedZone = zones.find(zone =>
      coords.x >= zone.x && coords.x <= zone.x + zone.width &&
      coords.y >= zone.y && coords.y <= zone.y + zone.height
    )

    if (clickedZone) {
      setSelectedZone(clickedZone)
      return
    }

    setSelectedZone(null)
    setIsDrawing(true)
    setStartPoint(coords)
    setCurrentRect({ x: coords.x, y: coords.y, width: 0, height: 0 })
  }

  const handleMouseMove = (e) => {
    if (!isDrawing || !startPoint) return

    const coords = getCanvasCoordinates(e)
    const width = coords.x - startPoint.x
    const height = coords.y - startPoint.y

    setCurrentRect({
      x: width >= 0 ? startPoint.x : coords.x,
      y: height >= 0 ? startPoint.y : coords.y,
      width: Math.abs(width),
      height: Math.abs(height)
    })
  }

  const handleMouseUp = () => {
    if (!isDrawing || !currentRect) return

    setIsDrawing(false)

    // Only save if rectangle is big enough (minimum 20x20)
    if (currentRect.width >= 20 && currentRect.height >= 20) {
      setPendingZone(currentRect)
      setShowNameModal(true)
    }

    setCurrentRect(null)
    setStartPoint(null)
  }

  const handleSaveZone = () => {
    if (pendingZone && zoneName.trim()) {
      saveZone({
        ...pendingZone,
        name: zoneName.trim()
      })
      setShowNameModal(false)
      setPendingZone(null)
      setZoneName('')
    }
  }

  const handleCancelZone = () => {
    setShowNameModal(false)
    setPendingZone(null)
    setZoneName('')
  }

  // Draw zones on canvas
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    // Draw saved zones
    zones.forEach(zone => {
      const isSelected = selectedZone?._id === zone._id

      // Fill
      ctx.fillStyle = isSelected ? 'rgba(66, 133, 244, 0.3)' : 'rgba(66, 133, 244, 0.2)'
      ctx.fillRect(zone.x, zone.y, zone.width, zone.height)

      // Border
      ctx.strokeStyle = isSelected ? '#1a73e8' : '#4285f4'
      ctx.lineWidth = isSelected ? 3 : 2
      ctx.strokeRect(zone.x, zone.y, zone.width, zone.height)

      // Label
      ctx.fillStyle = '#fff'
      ctx.font = 'bold 14px Arial'
      const textWidth = ctx.measureText(zone.name).width
      ctx.fillStyle = 'rgba(0, 0, 0, 0.7)'
      ctx.fillRect(zone.x, zone.y - 22, textWidth + 10, 22)
      ctx.fillStyle = '#fff'
      ctx.fillText(zone.name, zone.x + 5, zone.y - 6)
    })

    // Draw current drawing rectangle
    if (currentRect && isDrawing) {
      ctx.fillStyle = 'rgba(76, 175, 80, 0.3)'
      ctx.fillRect(currentRect.x, currentRect.y, currentRect.width, currentRect.height)
      ctx.strokeStyle = '#4caf50'
      ctx.lineWidth = 2
      ctx.setLineDash([5, 5])
      ctx.strokeRect(currentRect.x, currentRect.y, currentRect.width, currentRect.height)
      ctx.setLineDash([])
    }
  }, [zones, currentRect, isDrawing, selectedZone])

  return (
    <div className="spatial-editor">
      <div className="editor-toolbar">
        <button
          className={`toolbar-button ${editMode ? 'active' : ''}`}
          onClick={() => setEditMode(!editMode)}
        >
          {editMode ? 'Exit Edit Mode' : 'Edit Zones'}
        </button>
        {selectedZone && (
          <button
            className="toolbar-button delete"
            onClick={() => deleteZone(selectedZone._id)}
          >
            Delete Zone
          </button>
        )}
      </div>

      <div className="editor-container" ref={containerRef}>
        {isActive ? (
          <img
            src={videoSrc}
            alt="Live video feed"
            className="editor-video"
          />
        ) : (
          <div className="editor-placeholder">
            <p>Start camera to edit zones</p>
          </div>
        )}
        <canvas
          ref={canvasRef}
          className={`editor-canvas ${editMode ? 'editing' : ''}`}
          width={640}
          height={480}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        />
      </div>

      <div className="zones-list">
        <h3>Zones ({zones.length})</h3>
        {zones.length === 0 ? (
          <p className="no-zones">No zones defined. {editMode ? 'Click and drag to create a zone.' : 'Enable edit mode to add zones.'}</p>
        ) : (
          <ul>
            {zones.map(zone => (
              <li
                key={zone._id}
                className={selectedZone?._id === zone._id ? 'selected' : ''}
                onClick={() => setSelectedZone(zone)}
              >
                <span className="zone-name">{zone.name}</span>
                <span className="zone-coords">
                  ({Math.round(zone.x)}, {Math.round(zone.y)}) - {Math.round(zone.width)}x{Math.round(zone.height)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Name Modal */}
      {showNameModal && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Name this zone</h3>
            <input
              type="text"
              value={zoneName}
              onChange={(e) => setZoneName(e.target.value)}
              placeholder="Enter zone name..."
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSaveZone()
                if (e.key === 'Escape') handleCancelZone()
              }}
            />
            <div className="modal-buttons">
              <button onClick={handleCancelZone} className="cancel-button">Cancel</button>
              <button onClick={handleSaveZone} className="save-button" disabled={!zoneName.trim()}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default SpatialEditor
