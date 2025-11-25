"""
Video Processing Module for IBC Tracking
Handles prerecorded video analysis with real-time streaming of results
"""
import cv2
import base64
import requests
import tempfile
import os
from ibc_tracker import StableIBCMapper


class VideoProcessor:
    """
    Processes prerecorded videos for IBC detection and tracking.
    Emits real-time results via WebSocket as frames are processed.
    """

    def __init__(self, roboflow_api_url, roboflow_api_key, socketio,
                 zones_collection=None, use_mongodb=False, ibc_fill_status=None):
        """
        Initialize the video processor

        Args:
            roboflow_api_url: URL for Roboflow API endpoint
            roboflow_api_key: API key for Roboflow
            socketio: SocketIO instance for real-time updates
            zones_collection: MongoDB collection for zones (optional)
            use_mongodb: Whether to use MongoDB for zone tracking
            ibc_fill_status: Dictionary to track IBC fill status (shared with main app)
        """
        self.roboflow_api_url = roboflow_api_url
        self.roboflow_api_key = roboflow_api_key
        self.socketio = socketio
        self.zones_collection = zones_collection
        self.use_mongodb = use_mongodb
        self.ibc_fill_status = ibc_fill_status if ibc_fill_status is not None else {}

    def process_video_file(self, video_file, process_ibc_tracking_func=None):
        """
        Process an uploaded video file for IBC tracking

        Args:
            video_file: FileStorage object from Flask request
            process_ibc_tracking_func: Optional function to process IBC tracking with zones

        Returns:
            dict: Processing results summary
        """
        # Save video to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
            video_file.save(tmp_file.name)
            temp_video_path = tmp_file.name

        try:
            # Open video with OpenCV
            video_cap = cv2.VideoCapture(temp_video_path)
            if not video_cap.isOpened():
                raise ValueError('Failed to open video file')

            # Get video properties
            total_frames = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = video_cap.get(cv2.CAP_PROP_FPS)

            # Initialize tracking
            video_ibc_tracker = StableIBCMapper(max_ibcs=2)
            frames_processed = 0
            detections_summary = []

            # Get zones for tracking (if available)
            zones_list = []
            if self.use_mongodb and self.zones_collection is not None:
                zones_list = list(self.zones_collection.find())

            print(f"Processing video: {total_frames} frames at {fps} FPS")

            # Emit start event
            self.socketio.emit('video_processing_start', {
                'total_frames': total_frames,
                'fps': fps
            })

            # Process frames (sample for faster processing)
            frame_skip = max(1, int(fps / 5))  # Process ~5 frames per second
            frame_count = 0

            while video_cap.isOpened():
                ret, frame = video_cap.read()
                if not ret:
                    break

                # Skip frames for faster processing
                if frame_count % frame_skip != 0:
                    frame_count += 1
                    continue

                # Process this frame
                result = self._process_frame(
                    frame,
                    frame_count,
                    fps,
                    video_ibc_tracker,
                    zones_list,
                    process_ibc_tracking_func
                )

                if result:
                    detections_summary.append(result)
                    frames_processed += 1

                frame_count += 1

            # Clean up
            video_cap.release()
            os.unlink(temp_video_path)

            # Emit completion event
            self.socketio.emit('video_processing_complete', {
                'total_frames': total_frames,
                'frames_processed': frames_processed,
                'ibc_status': {str(k): v for k, v in self.ibc_fill_status.items()}
            })

            # Return summary
            return {
                'status': 'success',
                'total_frames': total_frames,
                'frames_processed': frames_processed,
                'fps': fps,
                'duration': total_frames / fps if fps > 0 else 0,
                'detections': detections_summary,
                'ibc_status': {str(k): v for k, v in self.ibc_fill_status.items()}
            }

        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(temp_video_path):
                os.unlink(temp_video_path)
            raise e

    def _process_frame(self, frame, frame_count, fps, tracker, zones_list,
                      process_ibc_tracking_func):
        """
        Process a single frame for detections and tracking

        Args:
            frame: OpenCV frame
            frame_count: Current frame number
            fps: Video frames per second
            tracker: StableIBCMapper instance
            zones_list: List of zone definitions
            process_ibc_tracking_func: Function to process zone tracking

        Returns:
            dict: Frame processing results or None if error
        """
        try:
            # Encode frame to base64 for Roboflow API
            _, buffer = cv2.imencode('.jpg', frame)
            img_base64 = base64.b64encode(buffer).decode('utf-8')

            # Call Roboflow API
            response = requests.post(
                self.roboflow_api_url,
                json={
                    "api_key": self.roboflow_api_key,
                    "image": {"type": "base64", "value": img_base64}
                },
                timeout=10
            )

            if not response.ok:
                print(f"Roboflow API error: {response.status_code}")
                return None

            result = response.json()
            predictions = result.get('predictions', [])

            # Extract detection boxes
            boxes = self._extract_boxes_from_predictions(predictions)

            # Track IBCs with stable IDs
            centroids = [box['centroid'] for box in boxes]
            stable_ibcs = tracker.update(centroids)

            # Draw bounding boxes on frame
            frame_with_boxes = self._draw_bounding_boxes(frame, boxes, stable_ibcs)

            # Encode annotated frame
            _, buffer = cv2.imencode('.jpg', frame_with_boxes)
            frame_base64 = base64.b64encode(buffer).decode('utf-8')

            # Process tracking and zone occupancy (if function provided)
            if len(boxes) > 0 and process_ibc_tracking_func and len(zones_list) > 0:
                import time
                current_time = time.time()
                process_ibc_tracking_func(boxes, zones_list, current_time, model=None)

            # Emit frame with detections via WebSocket
            self.socketio.emit('video_frame_processed', {
                'frame': frame_count,
                'frames_processed': int(frame_count / max(1, int(fps / 5))) + 1,
                'total_frames': int(frame_count),  # Will be updated by caller
                'image': frame_base64,
                'detections': len(boxes),
                'ibcs': {str(k): list(v) for k, v in stable_ibcs.items()}
            })

            # Return frame summary
            return {
                'frame': frame_count,
                'timestamp': frame_count / fps if fps > 0 else 0,
                'ibc_count': len(stable_ibcs),
                'ibcs': {str(ibc_id): list(centroid) for ibc_id, centroid in stable_ibcs.items()}
            }

        except Exception as e:
            print(f"Error processing frame {frame_count}: {e}")
            return None

    def _extract_boxes_from_predictions(self, predictions):
        """
        Extract bounding boxes from Roboflow predictions

        Args:
            predictions: List of prediction dicts from Roboflow API

        Returns:
            list: List of box dictionaries with bbox, confidence, class, centroid
        """
        boxes = []
        for pred in predictions:
            x = pred.get('x', 0)
            y = pred.get('y', 0)
            w = pred.get('width', 0)
            h = pred.get('height', 0)
            conf = pred.get('confidence', 0)
            class_name = pred.get('class', 'unknown')

            boxes.append({
                'box': [x - w/2, y - h/2, x + w/2, y + h/2],
                'confidence': conf,
                'class': class_name,
                'centroid': (x, y)
            })

        return boxes

    def _draw_bounding_boxes(self, frame, boxes, stable_ibcs):
        """
        Draw bounding boxes with IBC IDs on frame

        Args:
            frame: OpenCV frame
            boxes: List of detection boxes
            stable_ibcs: Dict of {ibc_id: centroid} from tracker

        Returns:
            Annotated frame with bounding boxes
        """
        frame_with_boxes = frame.copy()

        # Create mapping from centroid to stable IBC ID
        centroid_to_ibc = {}
        for ibc_id, centroid in stable_ibcs.items():
            centroid_to_ibc[centroid] = ibc_id

        # Draw each box
        for box_data in boxes:
            box = box_data['box']
            conf = box_data['confidence']
            class_name = box_data['class']
            centroid = box_data['centroid']

            # Find which IBC this box belongs to
            ibc_id = centroid_to_ibc.get(centroid, None)

            # Update fill status
            if ibc_id:
                self.ibc_fill_status[ibc_id] = class_name

            # Draw rectangle with color coding
            x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
            # Green for IBC-1, Red for IBC-2, Gray for untracked
            color = (0, 255, 0) if ibc_id == 1 else (255, 0, 0) if ibc_id == 2 else (128, 128, 128)
            cv2.rectangle(frame_with_boxes, (x1, y1), (x2, y2), color, 2)

            # Draw label
            label = f"IBC-{ibc_id}: {class_name} {conf:.2f}" if ibc_id else f"{class_name} {conf:.2f}"
            cv2.putText(frame_with_boxes, label, (x1, y1 - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        return frame_with_boxes
