"""
Train YOLOv11 model for Pharma Demo detection
Dataset: pharma-demo-53exj v2 from Roboflow
"""
from roboflow import Roboflow
from ultralytics import YOLO
import os
import shutil

print("=" * 60)
print("Training YOLOv11 Pharma Detection Model")
print("=" * 60)

# Configuration
API_KEY = "g3kyzU8K82YQwalVS2Ks"
WORKSPACE = "die-counter"
PROJECT_NAME = "pharma-demo-53exj"
VERSION = 9
MODEL = "yolo11s.pt"  # YOLOv11 small - better accuracy than nano

print(f"\nConfiguration:")
print(f"  Workspace: {WORKSPACE}")
print(f"  Project: {PROJECT_NAME}")
print(f"  Version: {VERSION}")
print(f"  Base Model: {MODEL}")
print("=" * 60)

# Download dataset from Roboflow
print(f"\n1. Downloading dataset from Roboflow...")
rf = Roboflow(api_key=API_KEY)
project = rf.workspace(WORKSPACE).project(PROJECT_NAME)
version = project.version(VERSION)
dataset = version.download("yolov11")

DATASET_PATH = dataset.location
print(f"   Dataset downloaded to: {DATASET_PATH}")

# Load base model
print(f"\n2. Loading {MODEL}...")
model = YOLO(MODEL)

# Train the model
print("\n3. Starting training...")
print("   This may take 10-30 minutes depending on your hardware.\n")

results = model.train(
    data=f"{DATASET_PATH}/data.yaml",
    epochs=50,          # Can increase for better accuracy
    imgsz=640,
    batch=8,            # Smaller batch for Mac memory
    name='pharma-yolo11',
    patience=10,        # Early stopping
    device='mps',       # Apple Silicon GPU
    verbose=True,
)

print("\n" + "=" * 60)
print("Training Complete!")
print("=" * 60)

# Find the best model (handle numbered directories)
import glob
model_dirs = glob.glob("runs/detect/pharma-yolo11*/weights/best.pt")
if model_dirs:
    best_model_path = max(model_dirs, key=os.path.getctime)
else:
    best_model_path = "runs/detect/pharma-yolo11/weights/best.pt"

print(f"\nBest model at: {best_model_path}")

# Copy best model to convenient location
final_model_path = os.path.join(os.path.dirname(__file__), "pharma_model.pt")
if os.path.exists(best_model_path):
    shutil.copy(best_model_path, final_model_path)
    print(f"Model copied to: {final_model_path}")

    # Test the model
    print("\n4. Testing model...")
    test_model = YOLO(final_model_path)
    print(f"   Classes: {test_model.names}")

print("\n" + "=" * 60)
print("Done! The model is ready at: pharma_model.pt")
print("Restart server_local.py to use the new model.")
print("=" * 60)
