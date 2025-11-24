"""
Capture frames from camera feed at regular intervals
This helps build a diverse training dataset with different lighting conditions
"""
import cv2
import os
import time
from datetime import datetime
import argparse

def capture_frames(output_dir='captured_frames', interval_seconds=5, duration_minutes=None, camera_index=0):
    """
    Capture frames from camera at regular intervals

    Args:
        output_dir: Directory to save captured frames
        interval_seconds: Time between captures (seconds)
        duration_minutes: How long to run (None = run until Ctrl+C)
        camera_index: Camera device index (0 for default)
    """
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Open camera
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print("Error: Could not open camera")
        return

    # Set resolution (same as your deployment)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print("=" * 60)
    print("Frame Capture Tool")
    print("=" * 60)
    print(f"Output directory: {output_dir}")
    print(f"Capture interval: {interval_seconds} seconds")
    print(f"Duration: {'Unlimited (press Ctrl+C to stop)' if duration_minutes is None else f'{duration_minutes} minutes'}")
    print("=" * 60)
    print("\nCapturing frames... Press Ctrl+C to stop\n")

    start_time = time.time()
    frame_count = 0

    try:
        while True:
            # Check duration limit
            if duration_minutes is not None:
                elapsed_minutes = (time.time() - start_time) / 60
                if elapsed_minutes >= duration_minutes:
                    print(f"\nDuration limit reached ({duration_minutes} minutes)")
                    break

            # Capture frame
            ret, frame = cap.read()
            if not ret:
                print("Error: Failed to capture frame")
                break

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"frame_{timestamp}_{frame_count:04d}.jpg"
            filepath = os.path.join(output_dir, filename)

            # Save frame
            cv2.imwrite(filepath, frame)
            frame_count += 1

            # Print status
            elapsed = time.time() - start_time
            print(f"[{elapsed:.1f}s] Captured frame {frame_count}: {filename}")

            # Wait for next interval
            time.sleep(interval_seconds)

    except KeyboardInterrupt:
        print("\n\nCapture stopped by user (Ctrl+C)")

    finally:
        # Cleanup
        cap.release()
        print("\n" + "=" * 60)
        print(f"Capture complete!")
        print(f"Total frames captured: {frame_count}")
        print(f"Saved to: {output_dir}")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Review the captured frames")
        print("2. Upload them to Roboflow: https://app.roboflow.com/die-counter/pharma-demo-53exj")
        print("3. Annotate the IBCs in each frame")
        print("4. Create a new version with the combined dataset")
        print("5. Retrain the model")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Capture frames from camera at regular intervals')
    parser.add_argument('--output', '-o', default='captured_frames',
                        help='Output directory (default: captured_frames)')
    parser.add_argument('--interval', '-i', type=int, default=5,
                        help='Seconds between captures (default: 5)')
    parser.add_argument('--duration', '-d', type=int, default=None,
                        help='Duration in minutes (default: unlimited)')
    parser.add_argument('--camera', '-c', type=int, default=0,
                        help='Camera index (default: 0)')

    args = parser.parse_args()

    capture_frames(
        output_dir=args.output,
        interval_seconds=args.interval,
        duration_minutes=args.duration,
        camera_index=args.camera
    )
