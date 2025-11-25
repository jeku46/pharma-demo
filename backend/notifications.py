"""
Push notification service using Pushover API
"""
import os
import requests
from datetime import datetime


class PushoverNotifier:
    """Handles push notifications via Pushover API"""

    PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

    def __init__(self):
        self.user_key = os.environ.get('PUSHOVER_USER_KEY', '')
        self.api_token = os.environ.get('PUSHOVER_API_TOKEN', '')
        self.enabled = bool(self.user_key and self.api_token)

        if not self.enabled:
            print("⚠️  Pushover notifications disabled - missing PUSHOVER_USER_KEY or PUSHOVER_API_TOKEN")
        else:
            print("✓ Pushover notifications enabled")

    def send_notification(self, title, message, priority=0, sound="pushover"):
        """
        Send a push notification via Pushover

        Args:
            title: Notification title
            message: Notification body text
            priority: -2 (lowest) to 2 (emergency). Default 0 (normal)
                     -2: No notification/alert
                     -1: Quiet notification
                      0: Normal priority
                      1: High priority (bypasses quiet hours)
                      2: Emergency (requires acknowledgment)
            sound: Notification sound (pushover, bike, bugle, cashregister,
                   classical, cosmic, falling, gamelan, incoming, intermission,
                   magic, mechanical, pianobar, siren, spacealarm, tugboat,
                   alien, climb, persistent, echo, updown, vibrate, none)

        Returns:
            bool: True if notification sent successfully, False otherwise
        """
        if not self.enabled:
            print(f"[Pushover disabled] Would send: {title} - {message}")
            return False

        try:
            payload = {
                "token": self.api_token,
                "user": self.user_key,
                "title": title,
                "message": message,
                "priority": priority,
                "sound": sound
            }

            # Emergency priority requires retry and expire parameters
            if priority == 2:
                payload["retry"] = 60  # Retry every 60 seconds
                payload["expire"] = 300  # Stop retrying after 5 minutes

            response = requests.post(self.PUSHOVER_API_URL, data=payload, timeout=10)

            if response.ok:
                print(f"✓ Pushover notification sent: {title}")
                return True
            else:
                print(f"✗ Pushover error: {response.status_code} - {response.text}")
                return False

        except requests.exceptions.Timeout:
            print("✗ Pushover notification timeout")
            return False
        except Exception as e:
            print(f"✗ Pushover notification error: {e}")
            return False

    def notify_compliance_event(self, event_type, ibc_id, zone_name, reason, severity="high"):
        """
        Send notification for a compliance event

        Args:
            event_type: Type of violation (e.g., 'unwashed_entry', 'non_empty_washroom_entry')
            ibc_id: The IBC identifier
            zone_name: Name of the zone where violation occurred
            reason: Human-readable description of the violation
            severity: 'low', 'medium', or 'high'
        """
        # Map severity to Pushover priority
        priority_map = {
            "low": -1,
            "medium": 0,
            "high": 1
        }
        priority = priority_map.get(severity, 0)

        # Map severity to sound
        sound_map = {
            "low": "pushover",
            "medium": "falling",
            "high": "siren"
        }
        sound = sound_map.get(severity, "pushover")

        # Format the notification
        title = f"⚠️ GMP Compliance Alert"

        timestamp = datetime.now().strftime("%H:%M:%S")
        message = f"[{timestamp}] {reason}\n\nIBC: {ibc_id}\nZone: {zone_name}"

        return self.send_notification(title, message, priority=priority, sound=sound)


# Global notifier instance
_notifier = None


def get_notifier():
    """Get the global PushoverNotifier instance"""
    global _notifier
    if _notifier is None:
        _notifier = PushoverNotifier()
    return _notifier
