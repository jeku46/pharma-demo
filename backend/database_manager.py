"""
Database Manager for IBC Tracking System
Handles all MongoDB operations for zones, occupancy, IBCs, and compliance events
"""
from pymongo import MongoClient
from bson import ObjectId
import os
import json
import time


class DatabaseManager:
    """
    Manages all database operations for the IBC tracking system.
    Supports both MongoDB and JSON file fallback.
    """

    def __init__(self, mongo_uri=None, zones_file_path=None):
        """
        Initialize database manager

        Args:
            mongo_uri: MongoDB connection URI (optional)
            zones_file_path: Path to JSON file for zone storage fallback
        """
        self.mongo_uri = mongo_uri or os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
        self.zones_file = zones_file_path or os.path.join(os.path.dirname(__file__), 'zones.json')
        self.use_mongodb = False

        # Collections
        self.zones_collection = None
        self.occupancy_collection = None
        self.ibcs_collection = None
        self.compliance_events_collection = None

        # In-memory tracking state
        self.ibc_zone_state = {}  # {ibc_id: {zone_id: enter_time, ...}}
        self.active_occupancies = {}  # {(ibc_id, zone_id): mongo_doc_id}

        # Try to connect to MongoDB
        self._connect()

    def _connect(self):
        """Attempt to connect to MongoDB"""
        try:
            self.mongo_client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=2000)
            self.mongo_client.server_info()  # Test connection

            db = self.mongo_client['pharma_demo']
            self.zones_collection = db['zones']
            self.occupancy_collection = db['occupancy_intervals']
            self.ibcs_collection = db['ibcs']
            self.compliance_events_collection = db['compliance_events']

            self.use_mongodb = True
            print("MongoDB connected successfully")
        except Exception as e:
            print(f"MongoDB not available, using JSON file storage: {e}")
            self.use_mongodb = False

    # ============================================================
    # Zone Management
    # ============================================================

    def get_zones(self):
        """Get all zones"""
        if self.use_mongodb:
            zones = list(self.zones_collection.find())
            for z in zones:
                z['_id'] = str(z['_id'])
            return zones
        return self._load_zones_from_file()

    def create_zone(self, zone_data):
        """
        Create a new zone

        Args:
            zone_data: dict with keys: name, x, y, width, height

        Returns:
            Created zone dict with _id
        """
        zone = {
            'name': zone_data['name'],
            'x': float(zone_data['x']),
            'y': float(zone_data['y']),
            'width': float(zone_data['width']),
            'height': float(zone_data['height']),
            'created_at': time.time()
        }

        if self.use_mongodb:
            result = self.zones_collection.insert_one(zone)
            zone['_id'] = str(result.inserted_id)
        else:
            import uuid
            zone['_id'] = str(uuid.uuid4())
            zones = self._load_zones_from_file()
            zones.append(zone)
            self._save_zones_to_file(zones)

        return zone

    def update_zone(self, zone_id, update_data):
        """
        Update a zone

        Args:
            zone_id: Zone ID to update
            update_data: dict with fields to update

        Returns:
            Number of zones matched
        """
        update_data['updated_at'] = time.time()

        if self.use_mongodb:
            result = self.zones_collection.update_one(
                {'_id': ObjectId(zone_id)},
                {'$set': update_data}
            )
            return result.matched_count
        else:
            zones = self._load_zones_from_file()
            for zone in zones:
                if zone['_id'] == zone_id:
                    zone.update(update_data)
                    self._save_zones_to_file(zones)
                    return 1
            return 0

    def delete_zone(self, zone_id):
        """
        Delete a zone

        Args:
            zone_id: Zone ID to delete

        Returns:
            Number of zones deleted
        """
        if self.use_mongodb:
            result = self.zones_collection.delete_one({'_id': ObjectId(zone_id)})
            return result.deleted_count
        else:
            zones = self._load_zones_from_file()
            original_len = len(zones)
            zones = [z for z in zones if z['_id'] != zone_id]
            if len(zones) < original_len:
                self._save_zones_to_file(zones)
                return 1
            return 0

    def _load_zones_from_file(self):
        """Load zones from JSON file"""
        if os.path.exists(self.zones_file):
            with open(self.zones_file, 'r') as f:
                return json.load(f)
        return []

    def _save_zones_to_file(self, zones):
        """Save zones to JSON file"""
        with open(self.zones_file, 'w') as f:
            json.dump(zones, f, indent=2)

    # ============================================================
    # Occupancy Tracking
    # ============================================================

    def record_zone_entry(self, ibc_id, zone, enter_time):
        """
        Record an IBC entering a zone

        Args:
            ibc_id: IBC identifier
            zone: Zone dict with _id and name
            enter_time: Timestamp of entry

        Returns:
            Document ID if using MongoDB, None otherwise
        """
        doc = {
            'ibc_id': int(ibc_id),
            'zone_id': zone['_id'],
            'zone_name': zone['name'],
            'enter_time': enter_time,
            'exit_time': None,
            'duration': None
        }

        if self.use_mongodb and self.occupancy_collection is not None:
            result = self.occupancy_collection.insert_one(doc)
            self.active_occupancies[(ibc_id, zone['_id'])] = result.inserted_id
            print(f"IBC {ibc_id} ENTERED zone '{zone['name']}' at {enter_time}")
            return result.inserted_id

        return None

    def record_zone_exit(self, ibc_id, zone_id, exit_time):
        """
        Record an IBC exiting a zone

        Args:
            ibc_id: IBC identifier
            zone_id: Zone identifier
            exit_time: Timestamp of exit

        Returns:
            Duration of stay in seconds, or None
        """
        key = (ibc_id, zone_id)
        if key in self.active_occupancies and self.use_mongodb and self.occupancy_collection is not None:
            doc_id = self.active_occupancies[key]
            doc = self.occupancy_collection.find_one({'_id': doc_id})

            if doc:
                duration = exit_time - doc['enter_time']
                self.occupancy_collection.update_one(
                    {'_id': doc_id},
                    {'$set': {'exit_time': exit_time, 'duration': duration}}
                )
                print(f"IBC {ibc_id} EXITED zone '{doc['zone_name']}' after {duration:.1f}s")

                # Update last_cleaned if exiting washroom
                if doc['zone_name'].lower() == 'washroom' and self.ibcs_collection is not None:
                    self.ibcs_collection.update_one(
                        {'ibc_id': ibc_id},
                        {'$set': {'last_cleaned': exit_time}}
                    )
                    print(f"IBC {ibc_id} CLEANED (exited Washroom)")

                del self.active_occupancies[key]
                return duration

        return None

    def get_occupancy_intervals(self, ibc_id=None, zone_id=None, limit=100, active_only=False):
        """
        Get occupancy intervals with optional filters

        Args:
            ibc_id: Filter by IBC ID (optional)
            zone_id: Filter by zone ID (optional)
            limit: Maximum number of results
            active_only: Only return active (ongoing) occupancies

        Returns:
            List of occupancy interval dicts
        """
        if not self.use_mongodb or self.occupancy_collection is None:
            return []

        query = {}
        if ibc_id:
            query['ibc_id'] = int(ibc_id)
        if zone_id:
            query['zone_id'] = zone_id
        if active_only:
            query['exit_time'] = None

        intervals = list(self.occupancy_collection.find(query).sort('enter_time', -1).limit(limit))

        for interval in intervals:
            interval['_id'] = str(interval['_id'])

        return intervals

    def get_active_occupancies(self):
        """Get currently active occupancies"""
        if not self.use_mongodb or self.occupancy_collection is None:
            return []

        intervals = list(self.occupancy_collection.find({'exit_time': None}))
        for interval in intervals:
            interval['_id'] = str(interval['_id'])

        return intervals

    def clear_occupancy_history(self):
        """Clear all occupancy history"""
        if not self.use_mongodb or self.occupancy_collection is None:
            return 0

        result = self.occupancy_collection.delete_many({})
        self.ibc_zone_state = {}
        self.active_occupancies = {}

        print(f"Cleared {result.deleted_count} occupancy records")
        return result.deleted_count

    # ============================================================
    # IBC Tracking
    # ============================================================

    def update_ibc_tracking(self, ibc_id, current_time):
        """
        Update IBC first_seen and last_seen timestamps

        Args:
            ibc_id: IBC identifier
            current_time: Current timestamp
        """
        if not self.use_mongodb or self.ibcs_collection is None:
            return

        self.ibcs_collection.update_one(
            {'ibc_id': ibc_id},
            {
                '$set': {'last_seen': current_time},
                '$setOnInsert': {'first_seen': current_time, 'last_cleaned': None}
            },
            upsert=True
        )

    def get_ibcs(self):
        """Get all tracked IBCs"""
        if not self.use_mongodb or self.ibcs_collection is None:
            return []

        ibcs = list(self.ibcs_collection.find().sort('last_seen', -1))
        for ibc in ibcs:
            ibc['_id'] = str(ibc['_id'])

        return ibcs

    def get_ibc(self, ibc_id):
        """Get specific IBC details"""
        if not self.use_mongodb or self.ibcs_collection is None:
            return None

        ibc = self.ibcs_collection.find_one({'ibc_id': ibc_id})
        if ibc:
            ibc['_id'] = str(ibc['_id'])
        return ibc

    def clear_ibc_history(self):
        """Clear all IBC tracking history"""
        if not self.use_mongodb or self.ibcs_collection is None:
            return 0

        result = self.ibcs_collection.delete_many({})
        print(f"Cleared {result.deleted_count} IBC records")
        return result.deleted_count

    # ============================================================
    # Compliance Events
    # ============================================================

    def log_compliance_event(self, event_data):
        """
        Log a compliance violation event

        Args:
            event_data: dict with keys: timestamp, ibc_id, zone_id, zone_name,
                       event_type, severity, reason, etc.

        Returns:
            Inserted document ID or None
        """
        if not self.use_mongodb or self.compliance_events_collection is None:
            return None

        result = self.compliance_events_collection.insert_one(event_data)
        print(f"⚠️ COMPLIANCE VIOLATION: {event_data.get('reason', 'Unknown')}")
        return result.inserted_id

    def get_compliance_events(self, days=7, limit=100):
        """
        Get recent compliance events

        Args:
            days: Number of days to look back
            limit: Maximum number of events to return

        Returns:
            List of compliance event dicts
        """
        if not self.use_mongodb or self.compliance_events_collection is None:
            return []

        cutoff_time = time.time() - (days * 86400)
        events = list(self.compliance_events_collection.find({
            'timestamp': {'$gte': cutoff_time}
        }).sort('timestamp', -1).limit(limit))

        for event in events:
            event['_id'] = str(event['_id'])

        return events

    # ============================================================
    # Insights & Analytics
    # ============================================================

    def get_dwell_time_insights(self):
        """Get dwell time statistics by zone"""
        if not self.use_mongodb or self.occupancy_collection is None:
            return []

        pipeline = [
            {
                '$match': {
                    'exit_time': {'$exists': True}
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

        results = list(self.occupancy_collection.aggregate(pipeline))

        # Convert to human-readable format
        for r in results:
            r['zone_name'] = r.pop('_id')
            r['total_time_hours'] = r['total_time'] / 3600
            r['avg_time_minutes'] = r['avg_time'] / 60
            r['min_time_minutes'] = r['min_time'] / 60
            r['max_time_minutes'] = r['max_time'] / 60

        return results

    def get_wash_schedule(self):
        """Get IBCs that need washing soon"""
        if not self.use_mongodb or self.ibcs_collection is None:
            return []

        current_time = time.time()
        warning_threshold = current_time - (7 * 86400)  # 7 days ago

        ibcs_needing_wash = list(self.ibcs_collection.find({
            '$or': [
                {'last_cleaned': {'$exists': False}},
                {'last_cleaned': None},
                {'last_cleaned': {'$lt': warning_threshold}}
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

        return ibcs_needing_wash

    # ============================================================
    # Database Utilities
    # ============================================================

    def clear_all_data(self):
        """Clear all data from all collections"""
        deleted_counts = {}

        if not self.use_mongodb:
            return deleted_counts

        if self.occupancy_collection is not None:
            result = self.occupancy_collection.delete_many({})
            deleted_counts['occupancy'] = result.deleted_count

        if self.ibcs_collection is not None:
            result = self.ibcs_collection.delete_many({})
            deleted_counts['ibcs'] = result.deleted_count

        if self.compliance_events_collection is not None:
            result = self.compliance_events_collection.delete_many({})
            deleted_counts['compliance'] = result.deleted_count

        # Clear in-memory state
        self.active_occupancies.clear()
        self.ibc_zone_state.clear()

        print(f"Database cleared: {deleted_counts}")
        return deleted_counts
