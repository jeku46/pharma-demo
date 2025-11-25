"""
IBC Mapper - Maps tracked objects to known IBC identities (IBC-1, IBC-2)

This module ensures that all detected IBCs are consistently mapped to one of
the two known IBCs in the system, regardless of the tracker's internal IDs.
"""

import numpy as np


class IBCMapper:
    """Maps tracked object IDs to known IBC identities"""

    def __init__(self, num_ibcs=2):
        """
        Initialize the IBC mapper

        Args:
            num_ibcs: Number of known IBCs in the system (default: 2)
        """
        self.num_ibcs = num_ibcs
        # Known IBC identities: "IBC-1", "IBC-2"
        self.ibc_identities = [f"IBC-{i+1}" for i in range(num_ibcs)]

        # Track the last known position of each IBC
        # {ibc_identity: (x, y)}
        self.ibc_positions = {}

        # Map tracker IDs to IBC identities
        # {tracker_id: ibc_identity}
        self.tracker_to_ibc = {}

        # Track which IBCs are currently visible
        # {ibc_identity: tracker_id}
        self.active_ibcs = {}

        # Distance threshold for matching (pixels)
        self.distance_threshold = 300

    def map_detections(self, tracked_objects):
        """
        Map tracked objects to known IBC identities

        Args:
            tracked_objects: dict from tracker {tracker_id: (x, y)}

        Returns:
            dict mapping IBC identities to centroids: {ibc_identity: (x, y)}
        """
        if not tracked_objects:
            return {}

        # Get list of tracker IDs and positions
        tracker_ids = list(tracked_objects.keys())
        positions = np.array([tracked_objects[tid] for tid in tracker_ids])

        # If we have existing mappings, update positions
        for tracker_id, centroid in tracked_objects.items():
            if tracker_id in self.tracker_to_ibc:
                ibc_id = self.tracker_to_ibc[tracker_id]
                self.ibc_positions[ibc_id] = centroid
                self.active_ibcs[ibc_id] = tracker_id

        # Find unmapped tracker IDs
        unmapped_trackers = [tid for tid in tracker_ids if tid not in self.tracker_to_ibc]

        if unmapped_trackers:
            # Get available IBC identities (not currently active)
            available_ibcs = [ibc for ibc in self.ibc_identities if ibc not in self.active_ibcs]

            # If we have available IBC slots, assign to closest known positions or new slots
            for tracker_id in unmapped_trackers:
                centroid = tracked_objects[tracker_id]

                # If we have available IBC identities, use them
                if available_ibcs:
                    # Try to match to last known positions if they exist
                    if self.ibc_positions and available_ibcs:
                        best_ibc = None
                        best_distance = float('inf')

                        for ibc_id in available_ibcs:
                            if ibc_id in self.ibc_positions:
                                last_pos = self.ibc_positions[ibc_id]
                                distance = np.sqrt((centroid[0] - last_pos[0])**2 +
                                                 (centroid[1] - last_pos[1])**2)
                                if distance < best_distance and distance < self.distance_threshold:
                                    best_distance = distance
                                    best_ibc = ibc_id

                        # If we found a close match, use it
                        if best_ibc:
                            ibc_id = best_ibc
                            available_ibcs.remove(ibc_id)
                        else:
                            # Otherwise, use the first available
                            ibc_id = available_ibcs.pop(0)
                    else:
                        # No previous positions, just use first available
                        ibc_id = available_ibcs.pop(0)

                    self.tracker_to_ibc[tracker_id] = ibc_id
                    self.ibc_positions[ibc_id] = centroid
                    self.active_ibcs[ibc_id] = tracker_id

                else:
                    # All IBC slots are taken, reassign to closest IBC
                    # This handles cases where tracker loses an IBC and creates a new ID
                    best_ibc = None
                    best_distance = float('inf')

                    for ibc_id, active_tracker_id in self.active_ibcs.items():
                        if active_tracker_id in tracked_objects:
                            ibc_pos = tracked_objects[active_tracker_id]
                            distance = np.sqrt((centroid[0] - ibc_pos[0])**2 +
                                             (centroid[1] - ibc_pos[1])**2)
                            if distance < best_distance:
                                best_distance = distance
                                best_ibc = ibc_id

                    if best_ibc:
                        # This might be a lost IBC reappearing, check last known positions
                        for ibc_id, last_pos in self.ibc_positions.items():
                            if ibc_id not in self.active_ibcs:
                                distance = np.sqrt((centroid[0] - last_pos[0])**2 +
                                                 (centroid[1] - last_pos[1])**2)
                                if distance < self.distance_threshold:
                                    # Unmap the old tracker
                                    if ibc_id in self.active_ibcs:
                                        old_tracker = self.active_ibcs[ibc_id]
                                        if old_tracker in self.tracker_to_ibc:
                                            del self.tracker_to_ibc[old_tracker]

                                    # Map to this IBC
                                    self.tracker_to_ibc[tracker_id] = ibc_id
                                    self.ibc_positions[ibc_id] = centroid
                                    self.active_ibcs[ibc_id] = tracker_id
                                    best_ibc = None
                                    break

                    # If still unmapped and we determined a best_ibc, this is likely a duplicate detection
                    # We can skip it or log it
                    # if best_ibc and best_distance < 100:
                    #     print(f"Warning: Tracker {tracker_id} appears to be a duplicate of {best_ibc} (distance: {best_distance:.1f}px)")

        # Clean up inactive trackers
        active_tracker_ids = set(tracker_ids)
        for tracker_id in list(self.tracker_to_ibc.keys()):
            if tracker_id not in active_tracker_ids:
                ibc_id = self.tracker_to_ibc[tracker_id]
                del self.tracker_to_ibc[tracker_id]
                if ibc_id in self.active_ibcs and self.active_ibcs[ibc_id] == tracker_id:
                    del self.active_ibcs[ibc_id]

        # Build result: map IBC identities to current positions
        result = {}
        for tracker_id, ibc_id in self.tracker_to_ibc.items():
            if tracker_id in tracked_objects:
                result[ibc_id] = tracked_objects[tracker_id]

        return result

    def get_ibc_id(self, tracker_id):
        """Get the IBC identity for a tracker ID"""
        return self.tracker_to_ibc.get(tracker_id)

    def get_tracker_id(self, ibc_id):
        """Get the tracker ID for an IBC identity"""
        return self.active_ibcs.get(ibc_id)

    def reset(self):
        """Reset all mappings"""
        self.ibc_positions = {}
        self.tracker_to_ibc = {}
        self.active_ibcs = {}
        print("IBC mapper reset")
