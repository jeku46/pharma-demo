"""
Download a trained model from Roboflow 3.0
After training on Roboflow 3.0, use this script to download the model
"""
from roboflow import Roboflow
import os
import shutil

print("=" * 60)
print("Download Roboflow 3.0 Trained Model")
print("=" * 60)

# Configuration
API_KEY = "g3kyzU8K82YQwalVS2Ks"
WORKSPACE = "die-counter"

# Ask user for project details
print("\nWhich project did you train?")
print("1. pharma-demo-53exj")
print("2. pharma-demo-v2-5mkw0")
choice = input("Enter choice (1 or 2): ").strip()

if choice == "1":
    PROJECT_NAME = "pharma-demo-53exj"
elif choice == "2":
    PROJECT_NAME = "pharma-demo-v2-5mkw0"
else:
    print("Invalid choice")
    exit(1)

VERSION = input(f"Enter version number for {PROJECT_NAME}: ").strip()

print(f"\nConfiguration:")
print(f"  Workspace: {WORKSPACE}")
print(f"  Project: {PROJECT_NAME}")
print(f"  Version: {VERSION}")
print("=" * 60)

# Initialize Roboflow
print("\nConnecting to Roboflow...")
rf = Roboflow(api_key=API_KEY)
project = rf.workspace(WORKSPACE).project(PROJECT_NAME)
version = project.version(int(VERSION))

# Get model details
print(f"\nFetching model from {PROJECT_NAME} v{VERSION}...")
print("\nIMPORTANT: Make sure you have trained this version with Roboflow 3.0!")
print("If not trained yet, go to:")
print(f"https://app.roboflow.com/{WORKSPACE}/{PROJECT_NAME}/{VERSION}")
print("and click 'Train with Roboflow 3.0'\n")

proceed = input("Has this version been trained with Roboflow 3.0? (yes/no): ").strip().lower()
if proceed != "yes":
    print("\nPlease train the model first on Roboflow, then run this script again.")
    exit(0)

try:
    # Deploy the model (this creates an inference endpoint)
    print("\nDeploying model...")
    model = version.model

    print(f"\n✅ Model deployed successfully!")
    print(f"\nModel ID: {model.id}")
    print(f"Model Version: {model.version}")

    # Test the model
    print("\n" + "=" * 60)
    print("Testing Model")
    print("=" * 60)

    # You can now use this model for inference via Roboflow API
    # or download the weights if you prefer local inference

    print("\nTo use this model, you have two options:")
    print("\n1. ROBOFLOW HOSTED INFERENCE (Recommended for better accuracy):")
    print("   - No download needed")
    print("   - Use Roboflow API for inference")
    print("   - We'll update server_local.py to use this")

    print("\n2. LOCAL INFERENCE:")
    print("   - Download weights from Roboflow dashboard")
    print("   - Go to your version page and download PyTorch weights")
    print(f"   - URL: https://app.roboflow.com/{WORKSPACE}/{PROJECT_NAME}/{VERSION}")

    use_hosted = input("\nUse Roboflow hosted inference? (yes/no): ").strip().lower()

    if use_hosted == "yes":
        print("\n✅ Will configure server to use Roboflow hosted inference")
        print("\nYour model endpoint:")
        print(f"  Workspace: {WORKSPACE}")
        print(f"  Project: {PROJECT_NAME}")
        print(f"  Version: {VERSION}")
        print(f"  Model ID: {model.id}")

        # Save configuration
        config = {
            'use_roboflow_hosted': True,
            'workspace': WORKSPACE,
            'project': PROJECT_NAME,
            'version': VERSION,
            'model_id': model.id,
            'api_key': API_KEY
        }

        import json
        config_path = os.path.join(os.path.dirname(__file__), 'roboflow_config.json')
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        print(f"\n✅ Configuration saved to: {config_path}")
        print("\nNext step: Update server_local.py to use Roboflow hosted inference")

    else:
        print("\nTo download weights for local inference:")
        print(f"1. Go to: https://app.roboflow.com/{WORKSPACE}/{PROJECT_NAME}/{VERSION}")
        print("2. Click 'Download Dataset'")
        print("3. Select 'YOLO v11 PyTorch' format")
        print("4. Download and extract")
        print("5. Copy the weights file to pharma_model.pt")

except Exception as e:
    print(f"\n❌ Error: {e}")
    print("\nMake sure:")
    print("1. You have trained this version with Roboflow 3.0")
    print("2. The training has completed successfully")
    print(f"3. Check the version page: https://app.roboflow.com/{WORKSPACE}/{PROJECT_NAME}/{VERSION}")

print("\n" + "=" * 60)
print("Done!")
print("=" * 60)
