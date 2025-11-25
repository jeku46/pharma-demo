"""
Helper script to list your Roboflow projects and versions
"""
from roboflow import Roboflow

# Initialize Roboflow with your API key
rf = Roboflow(api_key="g3kyzU8K82YQwalVS2Ks")

# Get workspace
workspace = rf.workspace("die-counter")

print("=" * 60)
print("ROBOFLOW PROJECTS IN WORKSPACE: die-counter")
print("=" * 60)

# List all projects
try:
    projects = workspace.projects()

    if not projects:
        print("No projects found in this workspace.")
    else:
        for project in projects:
            print(f"\nProject: {project.name}")
            print(f"  ID: {project.id}")
            print(f"  Type: {project.type}")

            try:
                # Try to get versions
                versions = project.versions()
                if versions:
                    print(f"  Versions:")
                    for version in versions:
                        print(f"    - Version {version.version}: {version.name}")
            except Exception as e:
                print(f"  Could not fetch versions: {e}")

except Exception as e:
    print(f"Error listing projects: {e}")
    print("\nAlternatively, you can:")
    print("1. Visit https://app.roboflow.com/die-counter")
    print("2. Find your project name")
    print("3. Click on your dataset to see the version number")
