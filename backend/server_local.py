"""
Gollum Detection Server using locally trained YOLO model
Supports both local webcam and IP camera (phone) sources
"""
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from ultralytics import YOLO
from pymongo import MongoClient
from bson import ObjectId
import cv2
import threading
import requests
import time
import os
import json

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables
latest_frame = None
latest_result = None
frame_lock = threading.Lock()
model = None
cap = None
camera_active = False
last_gollum_state = None
detection_thread = None
camera_source = None  # 0 for webcam, URL string for IP camera
confidence_threshold = 0.1  # Default confidence threshold

# LED control configuration
LED_BASE_URL = "http://10.0.0.106:5000"

# MongoDB configuration (with JSON file fallback)
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
ZONES_FILE = os.path.join(os.path.dirname(__file__), 'zones.json')
use_mongodb = True

try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    mongo_client.server_info()  # Test connection
    db = mongo_client['pharma_demo']
    zones_collection = db['zones']
    occupancy_collection = db['occupancy_intervals']
    ibcs_collection = db['ibcs']
    print("MongoDB connected successfully")
except Exception as e:
    print(f"MongoDB not available, using JSON file storage: {e}")
    use_mongodb = False
    zones_collection = None
    occupancy_collection = None
    ibcs_collection = None

# Tracking state for IBC occupancy
ibc_zone_state = {}  # {ibc_id: {zone_id: enter_time, ...}}
active_occupancies = {}  # {(ibc_id, zone_id): mongo_doc_id}

def load_zones_from_file():
    """Load zones from JSON file"""
    if os.path.exists(ZONES_FILE):
        with open(ZONES_FILE, 'r') as f:
            return json.load(f)
    return []

def save_zones_to_file(zones):
    """Save zones to JSON file"""
    with open(ZONES_FILE, 'w') as f:
        json.dump(zones, f, indent=2)

def get_box_center(box):
    """Get center point of a bounding box"""
    x1, y1, x2, y2 = box.xyxy[0].tolist()
    return ((x1 + x2) / 2, (y1 + y2) / 2)

def point_in_zone(point, zone):
    """Check if a point is inside a zone"""
    px, py = point
    return (zone['x'] <= px <= zone['x'] + zone['width'] and
            zone['y'] <= py <= zone['y'] + zone['height'])

def get_cached_zones():
    """Get zones from MongoDB or file"""
    if use_mongodb:
        zones = list(zones_collection.find())
        for z in zones:
            z['_id'] = str(z['_id'])
        return zones
    return load_zones_from_file()

def record_zone_entry(ibc_id, zone, enter_time):
    """Record an IBC entering a zone"""
    global active_occupancies

    doc = {
        'ibc_id': int(ibc_id),
        'zone_id': zone['_id'],
        'zone_name': zone['name'],
        'enter_time': enter_time,
        'exit_time': None,
        'duration': None
    }

    if use_mongodb and occupancy_collection is not None:
        result = occupancy_collection.insert_one(doc)
        active_occupancies[(ibc_id, zone['_id'])] = result.inserted_id
        print(f"IBC {ibc_id} ENTERED zone '{zone['name']}' at {enter_time}")

def update_ibc_tracking(ibc_id, current_time):
    """Update IBC first_seen and last_seen timestamps"""
    if not use_mongodb or ibcs_collection is None:
        return

    ibcs_collection.update_one(
        {'ibc_id': ibc_id},
        {
            '$set': {'last_seen': current_time},
            '$setOnInsert': {'first_seen': current_time, 'last_cleaned': None}
        },
        upsert=True
    )

def record_zone_exit(ibc_id, zone_id, exit_time):
    """Record an IBC exiting a zone"""
    global active_occupancies

    key = (ibc_id, zone_id)
    if key in active_occupancies and use_mongodb and occupancy_collection:
        doc_id = active_occupancies[key]
        # Get enter_time to calculate duration
        doc = occupancy_collection.find_one({'_id': doc_id})
        if doc:
            duration = exit_time - doc['enter_time']
            occupancy_collection.update_one(
                {'_id': doc_id},
                {'$set': {'exit_time': exit_time, 'duration': duration}}
            )
            print(f"IBC {ibc_id} EXITED zone '{doc['zone_name']}' after {duration:.1f}s")

            # If exiting Washroom, update last_cleaned timestamp
            if doc['zone_name'].lower() == 'washroom' and ibcs_collection is not None:
                ibcs_collection.update_one(
                    {'ibc_id': ibc_id},
                    {'$set': {'last_cleaned': exit_time}}
                )
                print(f"IBC {ibc_id} CLEANED (exited Washroom)")

        del active_occupancies[key]

def process_ibc_tracking(boxes, zones, current_time):
    """Process IBC positions and track zone occupancy"""
    global ibc_zone_state

    # Get current IBC positions
    current_ibc_zones = {}  # {ibc_id: set of zone_ids}

    for box in boxes:
        if box.id is None:
            continue

        ibc_id = int(box.id[0])
        center = get_box_center(box)

        # Update IBC tracking (first_seen, last_seen)
        update_ibc_tracking(ibc_id, current_time)

        current_ibc_zones[ibc_id] = set()

        for zone in zones:
            if point_in_zone(center, zone):
                current_ibc_zones[ibc_id].add(zone['_id'])

    # Check for entries and exits
    for ibc_id, current_zones in current_ibc_zones.items():
        previous_zones = ibc_zone_state.get(ibc_id, set())

        # New entries
        for zone_id in current_zones - previous_zones:
            zone = next((z for z in zones if z['_id'] == zone_id), None)
            if zone:
                record_zone_entry(ibc_id, zone, current_time)

        # Exits
        for zone_id in previous_zones - current_zones:
            record_zone_exit(ibc_id, zone_id, current_time)

        ibc_zone_state[ibc_id] = current_zones

    # Handle IBCs that disappeared
    for ibc_id in list(ibc_zone_state.keys()):
        if ibc_id not in current_ibc_zones:
            for zone_id in ibc_zone_state[ibc_id]:
                record_zone_exit(ibc_id, zone_id, current_time)
            del ibc_zone_state[ibc_id]

    # Return set of all occupied zone IDs
    occupied_zone_ids = set()
    for zones_set in ibc_zone_state.values():
        occupied_zone_ids.update(zones_set)
    return occupied_zone_ids

# Model path - will be set to trained model
MODEL_PATH = os.path.join(os.path.dirname(__file__), "pharma_model.pt")
# Fallback to gollum model if pharma_model.pt doesn't exist
if not os.path.exists(MODEL_PATH):
    MODEL_PATH = os.path.join(os.path.dirname(__file__), "gollum_model.pt")

def control_led(color, state):
    """Control LED on Raspberry Pi"""
    try:
        url = f"{LED_BASE_URL}/led/{color}/{state}"
        response = requests.post(url, timeout=2)
        print(f"LED Control: {color} {state} - Status: {response.status_code}")
        return response.json()
    except Exception as e:
        print(f"LED Control Error: {e}")
        return None

def turn_off_all_leds():
    """Turn off both LEDs"""
    control_led('red', 'off')
    control_led('green', 'off')

def detection_loop():
    """Main detection loop running in a separate thread"""
    global latest_frame, latest_result, last_gollum_state, camera_active, cap, model

    # Cache zones for performance
    zones = get_cached_zones()
    zone_refresh_time = time.time()

    while camera_active:
        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame")
            time.sleep(0.1)
            continue

        current_time = time.time()

        # Refresh zones every 5 seconds
        if current_time - zone_refresh_time > 5:
            zones = get_cached_zones()
            zone_refresh_time = current_time

        # Run detection with TRACKING (persist=True keeps IDs across frames)
        results = model.track(frame, conf=confidence_threshold, persist=True, verbose=False)

        # Get annotated frame
        annotated_frame = results[0].plot()

        # Process IBC tracking and zone occupancy
        occupied_zone_ids = set()
        if results[0].boxes is not None and len(zones) > 0:
            occupied_zone_ids = process_ibc_tracking(results[0].boxes, zones, current_time)

        # Check if target object was detected (IBC for pharma, gollum for original)
        target_found = False
        detected_classes = []
        tracked_ids = []
        for result in results:
            for box in result.boxes:
                class_id = int(box.cls[0])
                class_name = model.names[class_id]
                detected_classes.append(class_name)
                if box.id is not None:
                    tracked_ids.append(int(box.id[0]))
                target_found = True

        # Update shared frame
        with frame_lock:
            latest_frame = annotated_frame
            latest_result = {
                'target_found': target_found,
                'detected_classes': detected_classes,
                'tracked_ids': tracked_ids,
                'detections': len(results[0].boxes) if results else 0,
                'occupied_zone_ids': list(occupied_zone_ids)
            }

        # Emit zone occupancy on every frame (for real-time UI updates)
        socketio.emit('zone_occupancy', {
            'occupied_zone_ids': list(occupied_zone_ids),
            'timestamp': time.time()
        })

        # Only control LEDs and emit detection event if state changed
        if target_found != last_gollum_state:
            last_gollum_state = target_found

            # Control LEDs
            if target_found:
                control_led('red', 'on')
                control_led('green', 'off')
            else:
                control_led('red', 'off')
                control_led('green', 'on')

            # Emit WebSocket event (keep gollum_found for backward compatibility)
            socketio.emit('detection', {
                'gollum_found': target_found,
                'detected_classes': detected_classes,
                'timestamp': time.time()
            })

            classes_str = ', '.join(detected_classes) if detected_classes else 'none'
            print(f"Detection: {classes_str if target_found else 'nothing detected'}")

        # Small delay to prevent CPU overload
        time.sleep(0.033)  # ~30 FPS

def generate_frames():
    """Generator function to stream video frames"""
    global latest_frame

    while True:
        with frame_lock:
            if latest_frame is not None:
                # Encode frame as JPEG
                ret, buffer = cv2.imencode('.jpg', latest_frame)
                if ret:
                    frame = buffer.tobytes()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

        time.sleep(0.033)  # ~30 FPS

@app.route('/video_feed')
def video_feed():
    """Video streaming route"""
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/start_camera', methods=['POST'])
def start_camera():
    """Start the camera and detection

    Optional JSON body:
    - camera_url: URL for IP camera (e.g., "http://192.168.1.100:8080/video")
                  If not provided, uses local webcam (device 0)
    """
    global model, cap, camera_active, last_gollum_state, detection_thread, camera_source

    if camera_active:
        return jsonify({'status': 'already_running'})

    try:
        # Get camera source from request
        data = request.get_json(silent=True) or {}
        camera_url = data.get('camera_url')

        # Reset state
        last_gollum_state = None

        # Turn off all LEDs
        turn_off_all_leds()

        # Load model if not already loaded
        if model is None:
            print(f"Loading model from: {MODEL_PATH}")
            if not os.path.exists(MODEL_PATH):
                return jsonify({
                    'status': 'error',
                    'message': f'Model not found at {MODEL_PATH}. Please train the model first.'
                }), 500
            model = YOLO(MODEL_PATH)
            print(f"Model loaded! Classes: {model.names}")

        # Open camera - IP camera URL, device index, or default webcam
        device_index = data.get('device_index')

        if camera_url:
            print(f"Connecting to IP camera: {camera_url}")
            camera_source = camera_url
            cap = cv2.VideoCapture(camera_url)
        elif device_index is not None:
            print(f"Using camera device {device_index}")
            camera_source = f"device:{device_index}"
            # Use AVFoundation backend on macOS for better Continuity Camera support
            cap = cv2.VideoCapture(int(device_index), cv2.CAP_AVFOUNDATION)
        else:
            # Default to iPhone Continuity Camera (device 1) with AVFoundation
            print("Using iPhone Continuity Camera (device 1)")
            camera_source = "iPhone (device 1)"
            cap = cv2.VideoCapture(1, cv2.CAP_AVFOUNDATION)

            # Warmup: give Continuity Camera time to initialize
            import time as warmup_time
            for _ in range(10):
                ret, _ = cap.read()
                if ret:
                    break
                warmup_time.sleep(0.5)

            # Fallback to built-in webcam if iPhone not available
            if not cap.isOpened() or not ret:
                print("iPhone not available, falling back to built-in webcam (device 0)")
                cap.release()
                cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
                camera_source = "webcam (device 0)"

        if not cap.isOpened():
            camera_source = None
            return jsonify({
                'status': 'error',
                'message': f'Could not open camera: {camera_url or "webcam"}'
            }), 500

        camera_active = True

        # Start detection thread
        detection_thread = threading.Thread(target=detection_loop, daemon=True)
        detection_thread.start()

        source_description = camera_url if camera_url else (f'device:{device_index}' if device_index is not None else 'webcam')
        return jsonify({
            'status': 'started',
            'camera_source': source_description
        })
    except Exception as e:
        print(f"Error starting camera: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/stop_camera', methods=['POST'])
def stop_camera():
    """Stop the camera and detection"""
    global cap, camera_active, last_gollum_state, camera_source

    if not camera_active:
        return jsonify({'status': 'not_running'})

    try:
        camera_active = False

        # Wait for detection thread to stop
        time.sleep(0.2)

        # Release camera
        if cap:
            cap.release()
            cap = None

        last_gollum_state = None
        camera_source = None

        # Turn off all LEDs
        turn_off_all_leds()

        return jsonify({'status': 'stopped'})
    except Exception as e:
        print(f"Error stopping camera: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/status', methods=['GET'])
def status():
    """Get camera status"""
    return jsonify({
        'camera_active': camera_active,
        'gollum_found': last_gollum_state,
        'model_loaded': model is not None,
        'model_path': MODEL_PATH,
        'camera_source': camera_source if camera_source != 0 else 'webcam',
        'confidence': confidence_threshold
    })

@app.route('/set_confidence', methods=['POST'])
def set_confidence():
    """Set the detection confidence threshold"""
    global confidence_threshold

    data = request.get_json() or {}
    new_confidence = data.get('confidence')

    if new_confidence is None:
        return jsonify({'status': 'error', 'message': 'confidence is required'}), 400

    try:
        new_confidence = float(new_confidence)
        if not 0.0 <= new_confidence <= 1.0:
            return jsonify({'status': 'error', 'message': 'confidence must be between 0 and 1'}), 400

        confidence_threshold = new_confidence
        print(f"Confidence threshold updated to: {confidence_threshold}")
        return jsonify({'status': 'updated', 'confidence': confidence_threshold})
    except ValueError:
        return jsonify({'status': 'error', 'message': 'invalid confidence value'}), 400

# ============================================================
# Zones API - Spatial Editor
# ============================================================

@app.route('/zones', methods=['GET'])
def get_zones():
    """Get all zones"""
    try:
        if use_mongodb:
            zones = list(zones_collection.find())
            # Convert ObjectId to string for JSON serialization
            for zone in zones:
                zone['_id'] = str(zone['_id'])
        else:
            zones = load_zones_from_file()
        return jsonify({'zones': zones})
    except Exception as e:
        print(f"Error getting zones: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/zones', methods=['POST'])
def create_zone():
    """Create a new zone"""
    try:
        data = request.get_json() or {}

        # Validate required fields
        required_fields = ['name', 'x', 'y', 'width', 'height']
        for field in required_fields:
            if field not in data:
                return jsonify({'status': 'error', 'message': f'{field} is required'}), 400

        zone = {
            'name': data['name'],
            'x': float(data['x']),
            'y': float(data['y']),
            'width': float(data['width']),
            'height': float(data['height']),
            'created_at': time.time()
        }

        if use_mongodb:
            result = zones_collection.insert_one(zone)
            zone['_id'] = str(result.inserted_id)
        else:
            import uuid
            zone['_id'] = str(uuid.uuid4())
            zones = load_zones_from_file()
            zones.append(zone)
            save_zones_to_file(zones)

        print(f"Zone created: {zone['name']}")
        return jsonify({'status': 'created', 'zone': zone}), 201
    except Exception as e:
        print(f"Error creating zone: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/zones/<zone_id>', methods=['PUT'])
def update_zone(zone_id):
    """Update a zone"""
    try:
        data = request.get_json() or {}

        update_data = {}
        for field in ['name', 'x', 'y', 'width', 'height']:
            if field in data:
                if field == 'name':
                    update_data[field] = data[field]
                else:
                    update_data[field] = float(data[field])

        if not update_data:
            return jsonify({'status': 'error', 'message': 'No fields to update'}), 400

        update_data['updated_at'] = time.time()

        result = zones_collection.update_one(
            {'_id': ObjectId(zone_id)},
            {'$set': update_data}
        )

        if result.matched_count == 0:
            return jsonify({'status': 'error', 'message': 'Zone not found'}), 404

        print(f"Zone updated: {zone_id}")
        return jsonify({'status': 'updated', 'zone_id': zone_id})
    except Exception as e:
        print(f"Error updating zone: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/zones/<zone_id>', methods=['DELETE'])
def delete_zone(zone_id):
    """Delete a zone"""
    try:
        if use_mongodb:
            result = zones_collection.delete_one({'_id': ObjectId(zone_id)})
            if result.deleted_count == 0:
                return jsonify({'status': 'error', 'message': 'Zone not found'}), 404
        else:
            zones = load_zones_from_file()
            original_len = len(zones)
            zones = [z for z in zones if z['_id'] != zone_id]
            if len(zones) == original_len:
                return jsonify({'status': 'error', 'message': 'Zone not found'}), 404
            save_zones_to_file(zones)

        print(f"Zone deleted: {zone_id}")
        return jsonify({'status': 'deleted', 'zone_id': zone_id})
    except Exception as e:
        print(f"Error deleting zone: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================================
# Occupancy Intervals API
# ============================================================

@app.route('/occupancy', methods=['GET'])
def get_occupancy():
    """Get occupancy intervals with optional filters"""
    try:
        if not use_mongodb or occupancy_collection is None:
            return jsonify({'intervals': [], 'message': 'MongoDB not available'})

        # Parse query parameters
        ibc_id = request.args.get('ibc_id')
        zone_id = request.args.get('zone_id')
        limit = int(request.args.get('limit', 100))
        active_only = request.args.get('active_only', 'false').lower() == 'true'

        # Build query
        query = {}
        if ibc_id:
            query['ibc_id'] = int(ibc_id)
        if zone_id:
            query['zone_id'] = zone_id
        if active_only:
            query['exit_time'] = None

        # Get intervals sorted by enter_time descending
        intervals = list(occupancy_collection.find(query).sort('enter_time', -1).limit(limit))

        # Convert ObjectId to string
        for interval in intervals:
            interval['_id'] = str(interval['_id'])

        return jsonify({'intervals': intervals})
    except Exception as e:
        print(f"Error getting occupancy: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/occupancy/active', methods=['GET'])
def get_active_occupancy():
    """Get currently active occupancies (IBCs currently in zones)"""
    try:
        if not use_mongodb or occupancy_collection is None:
            return jsonify({'active': []})

        intervals = list(occupancy_collection.find({'exit_time': None}))
        for interval in intervals:
            interval['_id'] = str(interval['_id'])

        return jsonify({'active': intervals})
    except Exception as e:
        print(f"Error getting active occupancy: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/occupancy/clear', methods=['POST'])
def clear_occupancy():
    """Clear all occupancy history"""
    try:
        if not use_mongodb or occupancy_collection is None:
            return jsonify({'status': 'error', 'message': 'MongoDB not available'}), 500

        result = occupancy_collection.delete_many({})
        # Reset tracking state
        global ibc_zone_state, active_occupancies
        ibc_zone_state = {}
        active_occupancies = {}

        print(f"Cleared {result.deleted_count} occupancy records")
        return jsonify({'status': 'cleared', 'deleted_count': result.deleted_count})
    except Exception as e:
        print(f"Error clearing occupancy: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================================
# IBCs API - Track individual IBCs
# ============================================================

@app.route('/ibcs', methods=['GET'])
def get_ibcs():
    """Get all tracked IBCs"""
    try:
        if not use_mongodb or ibcs_collection is None:
            return jsonify({'ibcs': [], 'message': 'MongoDB not available'})

        ibcs = list(ibcs_collection.find().sort('last_seen', -1))
        for ibc in ibcs:
            ibc['_id'] = str(ibc['_id'])

        return jsonify({'ibcs': ibcs})
    except Exception as e:
        print(f"Error getting IBCs: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/ibcs/<int:ibc_id>', methods=['GET'])
def get_ibc(ibc_id):
    """Get specific IBC details"""
    try:
        if not use_mongodb or ibcs_collection is None:
            return jsonify({'error': 'MongoDB not available'}), 500

        ibc = ibcs_collection.find_one({'ibc_id': ibc_id})
        if not ibc:
            return jsonify({'error': 'IBC not found'}), 404

        ibc['_id'] = str(ibc['_id'])
        return jsonify({'ibc': ibc})
    except Exception as e:
        print(f"Error getting IBC {ibc_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/ibcs/clear', methods=['POST'])
def clear_ibcs():
    """Clear all IBC tracking history"""
    try:
        if not use_mongodb or ibcs_collection is None:
            return jsonify({'status': 'error', 'message': 'MongoDB not available'}), 500

        result = ibcs_collection.delete_many({})
        print(f"Cleared {result.deleted_count} IBC records")
        return jsonify({'status': 'cleared', 'deleted_count': result.deleted_count})
    except Exception as e:
        print(f"Error clearing IBCs: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    print('Client connected')
    emit('connected', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    print('Client disconnected')

if __name__ == '__main__':
    print("=" * 60)
    print("Pharma Detection Server (Local YOLO Model)")
    print("=" * 60)
    print(f"Model path: {MODEL_PATH}")
    print(f"Model exists: {os.path.exists(MODEL_PATH)}")
    print("Server will be available at http://localhost:5001")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=5001, debug=True, allow_unsafe_werkzeug=True)
