"""
Gollum Detection Server using locally trained YOLO model
Supports both local webcam and IP camera (phone) sources
"""
from dotenv import load_dotenv
load_dotenv('../.env')  # Load environment variables from .env file

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from ultralytics import YOLO
from pymongo import MongoClient
from bson import ObjectId
from ibc_mapper import IBCMapper
from notifications import get_notifier
from compliance_checker import ComplianceChecker
import cv2
import threading
import requests
import time
import os
import json
import base64
import numpy as np

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
confidence_threshold = 0.5  # Default confidence threshold

# Roboflow API configuration
USE_ROBOFLOW_API = True  # Set to True to use Roboflow hosted inference
ROBOFLOW_API_KEY = os.environ.get('ROBOFLOW_API_KEY', '')
ROBOFLOW_WORKSPACE = "die-counter"
ROBOFLOW_PROJECT = "pharma-demo-v2-5mkw0"
ROBOFLOW_VERSION = 8
ROBOFLOW_API_URL = f"https://detect.roboflow.com/{ROBOFLOW_PROJECT}/{ROBOFLOW_VERSION}"

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
    compliance_events_collection = db['compliance_events']
    print("MongoDB connected successfully")
except Exception as e:
    print(f"MongoDB not available, using JSON file storage: {e}")
    use_mongodb = False
    zones_collection = None
    occupancy_collection = None
    ibcs_collection = None
    compliance_events_collection = None

# Tracking state for IBC occupancy
ibc_zone_state = {}  # {ibc_id: {zone_id: enter_time, ...}}
active_occupancies = {}  # {(ibc_id, zone_id): mongo_doc_id}
ibc_fill_status = {}  # {ibc_id: class_name} - Track fill status of each IBC
ibc_previous_fill_status = {}  # {ibc_id: class_name} - Track previous fill status to detect changes

# Initialize compliance checker (will be set up after MongoDB connection)
compliance_checker = None
if use_mongodb:
    compliance_checker = ComplianceChecker(
        ibcs_collection=ibcs_collection,
        compliance_events_collection=compliance_events_collection,
        notifier=get_notifier()
    )

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
    global active_occupancies, ibc_fill_status

    # Get current fill status
    fill_status_on_entry = ibc_fill_status.get(ibc_id, 'Unknown')

    doc = {
        'ibc_id': ibc_id,  # Store as string (IBC-1, IBC-2)
        'zone_id': zone['_id'],
        'zone_name': zone['name'],
        'enter_time': enter_time,
        'exit_time': None,
        'duration': None,
        'fill_status_on_entry': fill_status_on_entry,
        'fill_status_on_exit': None
    }

    if use_mongodb and occupancy_collection is not None:
        result = occupancy_collection.insert_one(doc)
        active_occupancies[(ibc_id, zone['_id'])] = result.inserted_id
        print(f"IBC {ibc_id} ({fill_status_on_entry}) ENTERED zone '{zone['name']}' at {enter_time}")

        # Check for compliance violations using the helper class
        if compliance_checker is not None:
            compliance_checker.check_zone_entry(ibc_id, zone, enter_time, fill_status_on_entry)

def update_ibc_tracking(ibc_id, current_time):
    """Update IBC first_seen and last_seen timestamps"""
    if not use_mongodb or ibcs_collection is None:
        return

    ibcs_collection.update_one(
        {'ibc_id': ibc_id},
        {
            '$set': {'last_seen': current_time},
            '$setOnInsert': {'first_seen': current_time, 'last_cleaned': None, 'needs_wash': None}
        },
        upsert=True
    )

def check_fill_status_change(ibc_id, new_status, current_time):
    """Check if IBC transitioned from filled to empty and set needs_wash time"""
    global ibc_previous_fill_status

    if not use_mongodb or ibcs_collection is None:
        return

    previous_status = ibc_previous_fill_status.get(ibc_id, '')

    # Check if transitioned from filled (containing API) to empty
    # "API" in the previous status means it was filled, "Empty" in new status means it's now empty
    was_filled = previous_status and 'api' in previous_status.lower() and 'empty' not in previous_status.lower()
    is_now_empty = new_status and 'empty' in new_status.lower()

    if was_filled and is_now_empty:
        # IBC just became empty after containing API - needs washing now
        ibcs_collection.update_one(
            {'ibc_id': ibc_id},
            {'$set': {'needs_wash': current_time}}
        )
        print(f"IBC {ibc_id} emptied (was {previous_status}) - needs wash set to now")

    # Update previous status
    ibc_previous_fill_status[ibc_id] = new_status

def record_zone_exit(ibc_id, zone_id, exit_time):
    """Record an IBC exiting a zone"""
    global active_occupancies, ibc_fill_status

    key = (ibc_id, zone_id)
    if key in active_occupancies and use_mongodb and occupancy_collection is not None:
        doc_id = active_occupancies[key]
        # Get enter_time to calculate duration and get current fill status
        doc = occupancy_collection.find_one({'_id': doc_id})
        if doc:
            duration = exit_time - doc['enter_time']
            fill_status_on_exit = ibc_fill_status.get(ibc_id, 'Unknown')

            occupancy_collection.update_one(
                {'_id': doc_id},
                {'$set': {
                    'exit_time': exit_time,
                    'duration': duration,
                    'fill_status_on_exit': fill_status_on_exit
                }}
            )
            print(f"IBC {ibc_id} ({fill_status_on_exit}) EXITED zone '{doc['zone_name']}' after {duration:.1f}s")

            # If exiting Washroom AND empty, update last_cleaned timestamp and clear needs_wash
            if doc['zone_name'].lower() == 'washroom' and ibcs_collection is not None:
                if fill_status_on_exit and 'empty' in fill_status_on_exit.lower():
                    ibcs_collection.update_one(
                        {'ibc_id': ibc_id},
                        {'$set': {'last_cleaned': exit_time, 'needs_wash': None}}
                    )
                    print(f"IBC {ibc_id} CLEANED (exited Washroom empty)")

        del active_occupancies[key]

def process_ibc_tracking(boxes, zones, current_time, model=None):
    """Process IBC positions and track zone occupancy"""
    global ibc_zone_state, ibc_fill_status

    # Get current IBC positions
    current_ibc_zones = {}  # {ibc_id: set of zone_ids}

    for box in boxes:
        if box.id is None:
            continue

        ibc_id = box.id[0]  # Keep as string (IBC-1, IBC-2) or int for local model
        center = get_box_center(box)

        # Capture fill status (class name) for this IBC
        if model is not None and hasattr(box, 'cls'):
            class_id = int(box.cls[0])
            class_name = model.names[class_id]
            check_fill_status_change(ibc_id, class_name, current_time)
            ibc_fill_status[ibc_id] = class_name

        # Update IBC tracking (first_seen, last_seen)
        update_ibc_tracking(ibc_id, current_time)

        current_ibc_zones[ibc_id] = set()

        for zone in zones:
            in_zone = point_in_zone(center, zone)
            if in_zone:
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

    # Handle IBCs that disappeared from detection
    # NOTE: We do NOT treat "not detected" as an exit. IBCs are always in frame,
    # so gaps in tracking should not end occupancy intervals. Only when an IBC
    # is detected OUTSIDE a zone (handled above in the exits loop) do we record an exit.
    # We keep the zone state for disappeared IBCs so occupancy continues.
    # (Removed: code that would record exits and delete state for missing IBCs)

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

# ============================================================
# Roboflow API Detection
# ============================================================

# Simple centroid tracker for maintaining IBC IDs across frames
class CentroidTracker:
    """Simple centroid-based object tracker"""
    def __init__(self, max_disappeared=60, max_objects=2):
        self.next_id = 1
        self.objects = {}  # {id: centroid}
        self.disappeared = {}  # {id: frame_count}
        self.last_seen = {}  # {id: timestamp} - track when each object was last seen
        self.max_disappeared = max_disappeared
        self.max_objects = max_objects

    def register(self, centroid):
        """Register a new object, removing oldest if at max capacity"""
        # If at max capacity, remove the oldest object
        if len(self.objects) >= self.max_objects:
            # Find the object with the oldest last_seen timestamp
            oldest_id = min(self.last_seen.keys(), key=lambda k: self.last_seen[k])
            print(f"Max objects reached, removing oldest object {oldest_id}")
            self.deregister(oldest_id)

        object_id = self.next_id
        self.objects[object_id] = centroid
        self.disappeared[object_id] = 0
        self.last_seen[object_id] = time.time()
        self.next_id += 1
        return object_id

    def deregister(self, object_id):
        """Deregister an object"""
        del self.objects[object_id]
        del self.disappeared[object_id]
        if object_id in self.last_seen:
            del self.last_seen[object_id]

    def update(self, detections):
        """
        Update tracker with new detections
        detections: list of centroids [(x, y), ...]
        Returns: dict of {object_id: centroid}
        """
        # If no detections, increment disappeared count
        if len(detections) == 0:
            for object_id in list(self.disappeared.keys()):
                self.disappeared[object_id] += 1
                if self.disappeared[object_id] > self.max_disappeared:
                    self.deregister(object_id)
            return self.objects

        # If no existing objects, register all detections (up to max)
        if len(self.objects) == 0:
            for centroid in detections[:self.max_objects]:
                self.register(centroid)
            return self.objects

        # Match detections to existing objects using distance
        object_ids = list(self.objects.keys())
        object_centroids = list(self.objects.values())

        import scipy.spatial.distance as dist
        D = dist.cdist(np.array(object_centroids), np.array(detections))

        # Find minimum distance pairs
        rows = D.min(axis=1).argsort()
        cols = D.argmin(axis=1)[rows]

        used_rows = set()
        used_cols = set()
        matches = []

        for (row, col) in zip(rows, cols):
            if row in used_rows or col in used_cols:
                continue
            if D[row, col] > 100:  # Max distance threshold (pixels)
                continue
            matches.append((row, col))
            used_rows.add(row)
            used_cols.add(col)

        # Update matched objects
        for (row, col) in matches:
            object_id = object_ids[row]
            self.objects[object_id] = detections[col]
            self.disappeared[object_id] = 0
            self.last_seen[object_id] = time.time()

        # Deregister disappeared objects first (before registering new ones)
        for row in range(len(object_centroids)):
            if row not in used_rows:
                object_id = object_ids[row]
                if object_id in self.disappeared:  # Check if still exists
                    self.disappeared[object_id] += 1
                    if self.disappeared[object_id] > self.max_disappeared:
                        self.deregister(object_id)

        # Register new objects (will auto-remove oldest if at capacity)
        for col in range(len(detections)):
            if col not in used_cols:
                self.register(detections[col])

        return self.objects

# Initialize tracker and IBC mapper
ibc_tracker = CentroidTracker(max_disappeared=60, max_objects=2)
ibc_mapper = IBCMapper(num_ibcs=2)  # We have 2 IBCs: IBC-1 and IBC-2

def detect_with_roboflow(frame, confidence_threshold):
    """
    Send frame to Roboflow API for inference
    Returns list of detections in format: [(x1, y1, x2, y2, confidence, class_name), ...]
    """
    try:
        # Encode frame to base64 (frame should already be resized to 640x480)
        _, buffer = cv2.imencode('.jpg', frame)
        img_base64 = base64.b64encode(buffer).decode('utf-8')

        # Call Roboflow API
        response = requests.post(
            ROBOFLOW_API_URL,
            params={
                "api_key": ROBOFLOW_API_KEY,
                "confidence": int(confidence_threshold * 100)
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=img_base64,
            timeout=5
        )

        if not response.ok:
            print(f"Roboflow API error: {response.status_code} - {response.text}")
            return []

        data = response.json()

        # Parse predictions
        detections = []
        predictions = data.get('predictions', [])

        for pred in predictions:
            # Roboflow format: {x, y, width, height, confidence, class}
            x_center = pred['x']
            y_center = pred['y']
            width = pred['width']
            height = pred['height']

            # Convert to x1, y1, x2, y2
            x1 = x_center - width / 2
            y1 = y_center - height / 2
            x2 = x_center + width / 2
            y2 = y_center + height / 2

            detections.append({
                'bbox': (x1, y1, x2, y2),
                'confidence': pred['confidence'],
                'class': pred['class'],
                'centroid': (x_center, y_center)
            })

        return detections

    except requests.exceptions.Timeout:
        print("Roboflow API timeout")
        return []
    except Exception as e:
        print(f"Roboflow API error: {e}")
        return []

def draw_detections(frame, detections, mapped_ibcs):
    """
    Draw bounding boxes and IBC IDs on frame
    detections: list of detection dicts from detect_with_roboflow
    mapped_ibcs: dict from mapper {ibc_identity: centroid} (e.g., {"IBC-1": (x, y), "IBC-2": (x, y)})
    Returns: annotated frame, list of (ibc_id, bbox) tuples
    """
    annotated_frame = frame.copy()

    # Map centroids to detection bboxes
    if len(detections) == 0 or len(mapped_ibcs) == 0:
        return annotated_frame, []

    detection_centroids = [d['centroid'] for d in detections]
    ibc_identities = list(mapped_ibcs.keys())
    ibc_centroids = list(mapped_ibcs.values())

    # Match mapped IBCs to detections
    import scipy.spatial.distance as dist
    D = dist.cdist(np.array(ibc_centroids), np.array(detection_centroids))

    matched_detections = []

    for i, ibc_id in enumerate(ibc_identities):
        if D.shape[1] == 0:
            break
        closest_detection_idx = D[i].argmin()
        if D[i, closest_detection_idx] < 100:  # Within threshold
            detection = detections[closest_detection_idx]
            x1, y1, x2, y2 = detection['bbox']

            # Draw bounding box
            cv2.rectangle(annotated_frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)

            # Draw IBC ID, class, and confidence (strip "IBC-" prefixes)
            ibc_display = ibc_id.replace("IBC-", "")
            class_display = detection['class'].replace("IBC-", "")
            label = f"{ibc_display}: {class_display} ({detection['confidence']:.2f})"
            cv2.putText(annotated_frame, label, (int(x1), int(y1) - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            matched_detections.append((ibc_id, detection['bbox']))

    return annotated_frame, matched_detections

def detection_loop():
    """Main detection loop running in a separate thread"""
    global latest_frame, latest_result, last_gollum_state, camera_active, cap, model, ibc_tracker, ibc_mapper, ibc_fill_status

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
        # ===== ROBOFLOW API DETECTION PATH =====
        # Resize frame to 640x480 to match zone coordinates
        resized_frame = cv2.resize(frame, (640, 480))

        # Get detections from Roboflow
        detections = detect_with_roboflow(resized_frame, confidence_threshold)

        # Keep only the top 2 detections by confidence
        if len(detections) > 2:
            detections = sorted(detections, key=lambda d: d['confidence'], reverse=True)[:2]

        # Update tracker with centroids
        centroids = [d['centroid'] for d in detections]
        tracked_objects = ibc_tracker.update(centroids)

        # Map tracker IDs to IBC identities (IBC-1, IBC-2)
        mapped_ibcs = ibc_mapper.map_detections(tracked_objects)

        # Debug logging (first frame only to avoid spam)
        if current_time - zone_refresh_time < 0.1:
            print(f"DEBUG: {len(zones)} zones loaded, {len(detections)} detections, {len(tracked_objects)} tracked objects, {len(mapped_ibcs)} mapped IBCs")

        # Draw detections with mapped IBC IDs on resized frame
        annotated_frame, matched_detections = draw_detections(resized_frame, detections, mapped_ibcs)

        # Process IBC tracking and zone occupancy
        # Create fake "boxes" structure for compatibility with existing zone tracking code
        class FakeBox:
            def __init__(self, ibc_id, bbox):
                self.id = [ibc_id]
                x1, y1, x2, y2 = bbox
                self.xyxy = [np.array([x1, y1, x2, y2])]

        # Build fake boxes from MAPPED IBCs (IBC-1, IBC-2) and their matched detections
        fake_boxes = []
        for ibc_identity, centroid in mapped_ibcs.items():
            # Find the detection closest to this IBC
            if len(detections) > 0:
                # Find detection with matching centroid
                min_dist = float('inf')
                best_detection = None
                for det in detections:
                    dx = det['centroid'][0] - centroid[0]
                    dy = det['centroid'][1] - centroid[1]
                    dist = (dx*dx + dy*dy) ** 0.5
                    if dist < min_dist and dist < 100:  # Within threshold
                        min_dist = dist
                        best_detection = det

                if best_detection:
                    fake_boxes.append(FakeBox(ibc_identity, best_detection['bbox']))
                    # Check for fill status change (API -> Empty triggers needs_wash)
                    check_fill_status_change(ibc_identity, best_detection['class'], current_time)
                    # Store fill status for this IBC (using IBC-1, IBC-2 as keys)
                    ibc_fill_status[ibc_identity] = best_detection['class']

        if current_time - zone_refresh_time < 0.1:
            print(f"DEBUG: Created {len(fake_boxes)} fake boxes")

        occupied_zone_ids = set()
        if len(fake_boxes) > 0 and len(zones) > 0:
            occupied_zone_ids = process_ibc_tracking(fake_boxes, zones, current_time)

        if current_time - zone_refresh_time < 0.1:
            print(f"DEBUG: {len(occupied_zone_ids)} zones occupied")

        # Check if target object was detected
        target_found = len(detections) > 0
        detected_classes = [d['class'] for d in detections]
        tracked_ids = list(mapped_ibcs.keys())  # Use mapped IBC identities (IBC-1, IBC-2)

        

        # Update shared frame (common for both paths)
        with frame_lock:
            latest_frame = annotated_frame
            latest_result = {
                'target_found': target_found,
                'detected_classes': detected_classes,
                'tracked_ids': tracked_ids,
                'detections': len(detected_classes),
                'occupied_zone_ids': list(occupied_zone_ids)
            }

        # Build zone-to-IBC mapping for frontend
        zone_ibc_mapping = {}  # {zone_id: [ibc_ids]}
        for ibc_id, zone_ids in ibc_zone_state.items():
            for zone_id in zone_ids:
                if zone_id not in zone_ibc_mapping:
                    zone_ibc_mapping[zone_id] = []
                zone_ibc_mapping[zone_id].append(ibc_id)

        # Determine which zones have non-compliant IBCs
        non_compliant_zone_ids = set()
        for zone in zones:
            zone_id = zone['_id']
            if zone_id in zone_ibc_mapping:
                for ibc_id in zone_ibc_mapping[zone_id]:
                    fill_status = ibc_fill_status.get(ibc_id, '')
                    if compliance_checker and compliance_checker.is_non_compliant(ibc_id, zone, fill_status):
                        non_compliant_zone_ids.add(zone_id)

        # Emit zone occupancy on every frame (for real-time UI updates)
        socketio.emit('zone_occupancy', {
            'occupied_zone_ids': list(occupied_zone_ids),
            'non_compliant_zone_ids': list(non_compliant_zone_ids),
            'zone_ibc_mapping': zone_ibc_mapping,
            'ibc_status': ibc_fill_status.copy(),  # Send IBC ID -> class name mapping
            'timestamp': time.time()
        })

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

        # Load model if not already loaded (only needed for local detection)
        if not USE_ROBOFLOW_API:
            if model is None:
                print(f"Loading model from: {MODEL_PATH}")
                if not os.path.exists(MODEL_PATH):
                    return jsonify({
                        'status': 'error',
                        'message': f'Model not found at {MODEL_PATH}. Please train the model first.'
                    }), 500
                model = YOLO(MODEL_PATH)
                print(f"Model loaded! Classes: {model.names}")
        else:
            print(f"Using Roboflow API: {ROBOFLOW_PROJECT}/{ROBOFLOW_VERSION}")

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

@app.route('/clear_database', methods=['POST'])
def clear_database():
    """Clear all MongoDB collections"""
    global active_occupancies, ibc_fill_status

    # Clear in-memory fill status tracking
    ibc_fill_status = {}

    if not use_mongodb or db is None:
        return jsonify({'status': 'error', 'message': 'MongoDB is not enabled'}), 400

    try:
        # Clear occupancy records
        if occupancy_collection is not None:
            result_occupancy = occupancy_collection.delete_many({})
            occupancy_deleted = result_occupancy.deleted_count
        else:
            occupancy_deleted = 0

        # Clear IBC records
        if ibcs_collection is not None:
            result_ibc = ibcs_collection.delete_many({})
            ibc_deleted = result_ibc.deleted_count
        else:
            ibc_deleted = 0

        # Clear compliance events
        if compliance_events_collection is not None:
            result_compliance = compliance_events_collection.delete_many({})
            compliance_deleted = result_compliance.deleted_count
        else:
            compliance_deleted = 0

        # Clear active occupancies in memory
        active_occupancies.clear()

        print(f"Database cleared: {occupancy_deleted} occupancy records, {ibc_deleted} IBC records, {compliance_deleted} compliance events")

        return jsonify({
            'status': 'cleared',
            'occupancy_records_deleted': occupancy_deleted,
            'ibc_records_deleted': ibc_deleted,
            'compliance_events_deleted': compliance_deleted
        })
    except Exception as e:
        print(f"Error clearing database: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

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
            query['ibc_id'] = ibc_id  # Keep as string (IBC-1, IBC-2)
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

@app.route('/ibcs/status', methods=['GET'])
def get_ibcs_status():
    """Get all IBCs with current status including zone, fill status, needs wash"""
    try:
        if not use_mongodb or ibcs_collection is None:
            return jsonify({'ibcs': [], 'message': 'MongoDB not available'})

        # Get all IBCs from database
        ibcs = list(ibcs_collection.find().sort('ibc_id', 1))

        # Get zones for lookup
        zones = get_cached_zones()
        zone_lookup = {z['_id']: z['name'] for z in zones}

        # Build status for each IBC
        ibc_statuses = []
        for ibc in ibcs:
            ibc_id = ibc['ibc_id']

            # Get current zone(s) from in-memory state
            current_zone_ids = ibc_zone_state.get(ibc_id, set())
            current_zones = [zone_lookup.get(zid, 'Unknown') for zid in current_zone_ids]

            # Get current fill status from in-memory state
            fill_status = ibc_fill_status.get(ibc_id, 'Unknown')

            # Determine if filled with API (not empty)
            filled_with = None
            if fill_status and 'empty' not in fill_status.lower():
                if 'api' in fill_status.lower():
                    filled_with = 'API'
                else:
                    filled_with = fill_status

            ibc_statuses.append({
                'ibc_id': ibc_id,
                'needs_wash': ibc.get('needs_wash') is not None,
                'needs_wash_since': ibc.get('needs_wash'),
                'last_cleaned': ibc.get('last_cleaned'),
                'fill_status': fill_status,
                'filled_with': filled_with,
                'current_zones': current_zones,
                'last_seen': ibc.get('last_seen')
            })

        return jsonify({'ibcs': ibc_statuses})
    except Exception as e:
        print(f"Error getting IBC status: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/ibcs/<ibc_id>', methods=['GET'])  # Accept string IBC IDs (IBC-1, IBC-2)
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

@app.route('/insights/dwell-time', methods=['GET'])
def get_dwell_time_insights():
    """Get dwell time statistics by zone"""
    try:
        if not use_mongodb or occupancy_collection is None:
            return jsonify({'dwell_times': [], 'message': 'MongoDB not available'})

        # Aggregate dwell time by zone
        pipeline = [
            {
                '$match': {
                    'exit_time': {'$exists': True}  # Only completed occupancies
                }
            },
            {
                '$addFields': {
                    'duration': {'$subtract': ['$exit_time', '$enter_time']}
                }
            },
            {
                '$group': {
                    '_id': '$zone_name',
                    'total_time': {'$sum': '$duration'},
                    'avg_time': {'$avg': '$duration'},
                    'min_time': {'$min': '$duration'},
                    'max_time': {'$max': '$duration'},
                    'count': {'$sum': 1}
                }
            },
            {
                '$sort': {'total_time': -1}
            }
        ]

        results = list(occupancy_collection.aggregate(pipeline))

        # Convert to human-readable format
        for r in results:
            r['zone_name'] = r.pop('_id')
            r['total_time_hours'] = r['total_time'] / 3600
            r['avg_time_minutes'] = r['avg_time'] / 60
            r['min_time_minutes'] = r['min_time'] / 60
            r['max_time_minutes'] = r['max_time'] / 60

        return jsonify({'dwell_times': results})
    except Exception as e:
        print(f"Error getting dwell time insights: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/insights/wash-schedule', methods=['GET'])
def get_wash_schedule():
    """Get IBCs that need washing soon"""
    try:
        if not use_mongodb or ibcs_collection is None:
            return jsonify({'wash_schedule': [], 'message': 'MongoDB not available'})

        current_time = time.time()
        # Define wash threshold (e.g., 24 hours = 86400 seconds)
        wash_threshold = 86400  # 24 hours
        warning_threshold = current_time - (7 * 86400)  # 7 days ago

        # Find IBCs that haven't been washed recently or never washed
        ibcs_needing_wash = list(ibcs_collection.find({
            '$or': [
                {'last_cleaned': {'$exists': False}},  # Never washed
                {'last_cleaned': None},  # Never washed
                {'last_cleaned': {'$lt': warning_threshold}}  # Not washed in 7 days
            ]
        }).sort('last_cleaned', 1))

        for ibc in ibcs_needing_wash:
            ibc['_id'] = str(ibc['_id'])
            if ibc.get('last_cleaned'):
                days_since_wash = (current_time - ibc['last_cleaned']) / 86400
                ibc['days_since_wash'] = round(days_since_wash, 1)
                ibc['urgency'] = 'high' if days_since_wash > 7 else 'medium'
            else:
                ibc['days_since_wash'] = None
                ibc['urgency'] = 'high'

        return jsonify({'wash_schedule': ibcs_needing_wash})
    except Exception as e:
        print(f"Error getting wash schedule: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/insights/compliance-events', methods=['GET'])
def get_compliance_events():
    """Get compliance violation events"""
    try:
        if not use_mongodb or compliance_events_collection is None:
            return jsonify({'events': [], 'message': 'MongoDB not available'})

        # Get recent compliance events (last 7 days)
        seven_days_ago = time.time() - (7 * 86400)
        events = list(compliance_events_collection.find({
            'timestamp': {'$gte': seven_days_ago}
        }).sort('timestamp', -1).limit(100))

        for event in events:
            event['_id'] = str(event['_id'])

        return jsonify({'events': events})
    except Exception as e:
        print(f"Error getting compliance events: {e}")
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
    if USE_ROBOFLOW_API:
        print("Pharma Detection Server (Roboflow API)")
        print("=" * 60)
        print(f"Roboflow Project: {ROBOFLOW_PROJECT}")
        print(f"Roboflow Version: {ROBOFLOW_VERSION}")
        print(f"API URL: {ROBOFLOW_API_URL}")
    else:
        print("Pharma Detection Server (Local YOLO Model)")
        print("=" * 60)
        print(f"Model path: {MODEL_PATH}")
        print(f"Model exists: {os.path.exists(MODEL_PATH)}")
    print("Server will be available at http://localhost:5001")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=5001, debug=True, allow_unsafe_werkzeug=True)
