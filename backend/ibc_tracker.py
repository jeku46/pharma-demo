"""
IBC Tracking System
Provides centroid-based object tracking and stable IBC ID mapping
"""
import numpy as np
import scipy.spatial.distance as dist


class CentroidTracker:
    """Simple centroid-based object tracker"""
    def __init__(self, max_disappeared=30):
        self.next_id = 1
        self.objects = {}  # {id: centroid}
        self.disappeared = {}  # {id: frame_count}
        self.max_disappeared = max_disappeared

    def register(self, centroid):
        """Register a new object"""
        object_id = self.next_id
        self.objects[object_id] = centroid
        self.disappeared[object_id] = 0
        self.next_id += 1
        return object_id

    def deregister(self, object_id):
        """Deregister an object"""
        del self.objects[object_id]
        del self.disappeared[object_id]

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

        # If no existing objects, register all detections
        if len(self.objects) == 0:
            for centroid in detections:
                self.register(centroid)
            return self.objects

        # Match detections to existing objects using distance
        object_ids = list(self.objects.keys())
        object_centroids = list(self.objects.values())

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

        # Register new objects
        for col in range(len(detections)):
            if col not in used_cols:
                self.register(detections[col])

        # Deregister disappeared objects
        for row in range(len(object_centroids)):
            if row not in used_rows:
                object_id = object_ids[row]
                self.disappeared[object_id] += 1
                if self.disappeared[object_id] > self.max_disappeared:
                    self.deregister(object_id)

        return self.objects


class StableIBCMapper:
    """
    Maps dynamic tracker IDs to stable IBC-1 and IBC-2 identifiers.
    Only tracks 2 IBCs max. When tracker assigns new IDs (due to losing track),
    reassigns them to IBC-1 or IBC-2 based on proximity to last known positions.
    """
    def __init__(self, max_ibcs=2):
        self.max_ibcs = max_ibcs
        self.tracker = CentroidTracker(max_disappeared=30)

        # Mapping from tracker ID -> stable IBC ID (1 or 2)
        self.tracker_to_stable = {}  # {tracker_id: 1 or 2}

        # Last known positions for stable IBCs
        self.stable_positions = {}  # {1: (x, y), 2: (x, y)}

        # Track which stable IDs are currently assigned
        self.assigned_stable_ids = set()  # {1, 2}

    def update(self, detections):
        """
        Update with new detections and return stable IBC mapping.
        Returns: dict {stable_id: centroid} where stable_id is 1 or 2
        """
        # Get tracker updates (tracker returns {tracker_id: centroid})
        tracker_objects = self.tracker.update(detections)

        # Build stable mapping
        stable_objects = {}  # {stable_id: centroid}

        # Process each tracked object
        for tracker_id, centroid in tracker_objects.items():
            # Check if we already have a mapping for this tracker ID
            if tracker_id in self.tracker_to_stable:
                stable_id = self.tracker_to_stable[tracker_id]
                stable_objects[stable_id] = centroid
                self.stable_positions[stable_id] = centroid
            else:
                # New tracker ID - need to assign to stable ID
                stable_id = self._assign_stable_id(centroid)
                if stable_id is not None:
                    self.tracker_to_stable[tracker_id] = stable_id
                    stable_objects[stable_id] = centroid
                    self.stable_positions[stable_id] = centroid

        # Clean up old tracker IDs that no longer exist
        active_tracker_ids = set(tracker_objects.keys())
        for tracker_id in list(self.tracker_to_stable.keys()):
            if tracker_id not in active_tracker_ids:
                # This tracker ID disappeared, free up its stable ID
                stable_id = self.tracker_to_stable[tracker_id]
                del self.tracker_to_stable[tracker_id]
                # Note: We keep the position in stable_positions for proximity matching

        # Update which stable IDs are currently assigned
        self.assigned_stable_ids = set(stable_objects.keys())

        return stable_objects

    def _assign_stable_id(self, centroid):
        """
        Assign a stable ID (1 or 2) to a new detection based on proximity.
        Returns stable_id (1 or 2) or None if all slots full.
        """
        # If no IBCs assigned yet, assign in order
        if len(self.assigned_stable_ids) == 0:
            return 1
        elif len(self.assigned_stable_ids) == 1:
            # Assign the other ID
            return 2 if 1 in self.assigned_stable_ids else 1

        # Both slots full - this is a re-identification case
        # Find which stable IBC this centroid is closest to
        if len(self.stable_positions) > 0:
            stable_ids = list(self.stable_positions.keys())
            positions = [self.stable_positions[sid] for sid in stable_ids]

            # Calculate distances to each stable IBC's last position
            distances = dist.cdist([centroid], positions)[0]

            # Find closest stable IBC
            closest_idx = distances.argmin()
            closest_stable_id = stable_ids[closest_idx]

            # Only reassign if not currently assigned
            if closest_stable_id not in self.assigned_stable_ids:
                return closest_stable_id

        # All slots full and already assigned, ignore this detection
        return None
