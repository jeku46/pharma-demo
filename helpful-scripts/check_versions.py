from roboflow import Roboflow

API_KEY = "g3kyzU8K82YQwalVS2Ks"
WORKSPACE = "die-counter"
PROJECT_NAME = "gollum-finder-b7c9n"

print(f"Checking project: {PROJECT_NAME}")
rf = Roboflow(api_key=API_KEY)
project = rf.workspace(WORKSPACE).project(PROJECT_NAME)

print(f"\nProject info:")
print(f"  Name: {project.name}")
print(f"  Type: {project.type}")

# Try to list versions
try:
    print(f"\nAvailable versions:")
    for i in range(1, 20):
        try:
            v = project.version(i)
            print(f"  Version {i}: {v}")
        except:
            break
except Exception as e:
    print(f"Error: {e}")

# Get project details
print(f"\nProject details: {project}")
