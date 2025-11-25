"""List all available cameras"""
import cv2

print("Checking available cameras...")
print("=" * 60)

for i in range(10):  # Check first 10 camera indices
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"Camera {i}: Active ({width}x{height})")
        else:
            print(f"Camera {i}: Found but can't read frames")
        cap.release()

print("=" * 60)
print("\nTo use a specific camera, run:")
print("python capture_frames.py --camera <index>")
