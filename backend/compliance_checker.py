"""
Compliance checker for IBC zone entry events.
Handles validation and logging of compliance violations.
"""


class ComplianceChecker:
    """
    Checks for compliance violations when IBCs enter zones.
    Logs violations to MongoDB and sends push notifications.
    """

    def __init__(self, ibcs_collection, compliance_events_collection, notifier):
        """
        Initialize the compliance checker.

        Args:
            ibcs_collection: MongoDB collection for IBC records
            compliance_events_collection: MongoDB collection for compliance events
            notifier: Notifier instance for sending push notifications
        """
        self.ibcs_collection = ibcs_collection
        self.compliance_events_collection = compliance_events_collection
        self.notifier = notifier

    def check_zone_entry(self, ibc_id, zone, enter_time, fill_status):
        """
        Check for compliance violations when an IBC enters a zone.

        Args:
            ibc_id: The IBC identifier (e.g., "IBC-1")
            zone: Zone dict with 'name', '_id' keys
            enter_time: Unix timestamp of entry
            fill_status: Current fill status of the IBC (e.g., "API", "Empty")

        Returns:
            List of violation events that were logged
        """
        violations = []

        # Check for unwashed IBC entering non-Washroom zone
        violation = self._check_unwashed_entry(ibc_id, zone, enter_time)
        if violation:
            violations.append(violation)

        # Check for unwashed IBC entering a Station zone
        violation = self._check_unwashed_station_entry(ibc_id, zone, enter_time)
        if violation:
            violations.append(violation)

        # Check for API-filled IBC entering Station 2
        violation = self._check_api_filled_station2_entry(ibc_id, zone, enter_time, fill_status)
        if violation:
            violations.append(violation)

        # Check for non-empty IBC entering Washroom
        violation = self._check_non_empty_washroom_entry(ibc_id, zone, enter_time, fill_status)
        if violation:
            violations.append(violation)

        return violations

    def _check_unwashed_entry(self, ibc_id, zone, enter_time):
        """Check if IBC needs washing and is entering a non-Washroom zone."""
        if zone['name'].lower() == 'washroom':
            return None

        if self.ibcs_collection is None:
            return None

        ibc = self.ibcs_collection.find_one({'ibc_id': ibc_id})
        if not ibc:
            return None

        # Check if IBC needs washing (only check if has been washed before)
        if not ibc.get('last_cleaned'):
            return None

        days_since_wash = (enter_time - ibc['last_cleaned']) / 86400
        if days_since_wash <= 7:
            return None

        reason = f"IBC not washed for {days_since_wash:.1f} days"
        event = {
            'timestamp': enter_time,
            'ibc_id': ibc_id,
            'zone_id': zone['_id'],
            'zone_name': zone['name'],
            'event_type': 'unwashed_entry',
            'severity': 'high',
            'reason': reason,
            'last_cleaned': ibc.get('last_cleaned')
        }

        self._log_violation(event)
        self._notify_violation('unwashed_entry', ibc_id, zone['name'], reason, 'high')

        return event

    def _check_unwashed_station_entry(self, ibc_id, zone, enter_time):
        """Check for unwashed IBC entering a Station zone."""
        if 'station' not in zone['name'].lower():
            return None

        if self.ibcs_collection is None:
            return None

        ibc = self.ibcs_collection.find_one({'ibc_id': ibc_id})
        if not ibc or not ibc.get('needs_wash'):
            return None

        reason = f"Unwashed IBC entered {zone['name']}"
        event = {
            'timestamp': enter_time,
            'ibc_id': ibc_id,
            'zone_id': zone['_id'],
            'zone_name': zone['name'],
            'event_type': 'unwashed_station_entry',
            'severity': 'high',
            'reason': reason,
            'needs_wash_since': ibc['needs_wash']
        }

        self._log_violation(event)
        self._notify_violation('unwashed_station_entry', ibc_id, zone['name'], reason, 'high')

        return event

    def _check_api_filled_station2_entry(self, ibc_id, zone, enter_time, fill_status):
        """Check for IBC filled with API entering Station 2."""
        if 'station 2' not in zone['name'].lower():
            return None

        if not fill_status:
            return None

        if 'api' not in fill_status.lower() or 'empty' in fill_status.lower():
            return None

        reason = f"IBC filled with API entered {zone['name']}"
        event = {
            'timestamp': enter_time,
            'ibc_id': ibc_id,
            'zone_id': zone['_id'],
            'zone_name': zone['name'],
            'event_type': 'api_filled_station2_entry',
            'severity': 'high',
            'reason': reason,
            'fill_status': fill_status
        }

        self._log_violation(event)
        self._notify_violation('api_filled_station2_entry', ibc_id, zone['name'], reason, 'high')

        return event

    def _check_non_empty_washroom_entry(self, ibc_id, zone, enter_time, fill_status):
        """Check for non-empty IBC entering Washroom."""
        if zone['name'].lower() != 'washroom':
            return None

        if not fill_status or fill_status.lower().endswith('empty'):
            return None

        reason = f"Non-empty IBC ({fill_status}) entered Washroom"
        event = {
            'timestamp': enter_time,
            'ibc_id': ibc_id,
            'zone_id': zone['_id'],
            'zone_name': zone['name'],
            'event_type': 'non_empty_washroom_entry',
            'severity': 'high',
            'reason': reason,
            'fill_status': fill_status
        }

        self._log_violation(event)
        self._notify_violation('non_empty_washroom_entry', ibc_id, zone['name'], reason, 'high')

        return event

    def is_non_compliant(self, ibc_id, zone, fill_status):
        """
        Check if an IBC is currently non-compliant in a zone (without logging).
        Used for real-time UI highlighting.

        Args:
            ibc_id: The IBC identifier (e.g., "IBC-1")
            zone: Zone dict with 'name', '_id' keys
            fill_status: Current fill status of the IBC

        Returns:
            True if the IBC is non-compliant in this zone
        """
        zone_name = zone['name'].lower()

        # Non-empty IBC in Washroom is non-compliant
        if zone_name == 'washroom':
            if fill_status and not fill_status.lower().endswith('empty'):
                return True

        # API-filled IBC in Station 2 is non-compliant
        if 'station 2' in zone_name:
            if fill_status and 'api' in fill_status.lower() and 'empty' not in fill_status.lower():
                return True

        # Unwashed IBC in Station zone is non-compliant
        if 'station' in zone_name and self.ibcs_collection is not None:
            ibc = self.ibcs_collection.find_one({'ibc_id': ibc_id})
            if ibc and ibc.get('needs_wash'):
                return True

        return False

    def _log_violation(self, event):
        """Log a compliance violation to MongoDB."""
        if self.compliance_events_collection is not None:
            self.compliance_events_collection.insert_one(event)
            print(f"⚠️ COMPLIANCE VIOLATION: {event['reason']}")

    def _notify_violation(self, event_type, ibc_id, zone_name, reason, severity):
        """Send push notification for a compliance violation."""
        if self.notifier:
            self.notifier.notify_compliance_event(
                event_type=event_type,
                ibc_id=ibc_id,
                zone_name=zone_name,
                reason=reason,
                severity=severity
            )
