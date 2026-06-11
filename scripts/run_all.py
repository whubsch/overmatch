#!/usr/bin/env python3
"""
Sequential script runner that executes Python scripts one after another.
Only proceeds to the next script if the previous one completes successfully.

Run from the root directory with:
    python3 -m scripts.run_all
"""

import subprocess
import sys
from pathlib import Path


def run_script(scripts: list[str]) -> bool:
    """
    Run a Python script and return whether it succeeded.

    Args:
        script_path: Path to the Python script to execute

    Returns:
        bool: True if script succeeded (exit code 0), False otherwise
    """
    print(f"\n{'=' * 60}")
    print(f"Running: {scripts}")
    print("=" * 60)

    try:
        subprocess.run(scripts, check=True, capture_output=False, text=True)
        print(f"\n✓ {scripts} completed successfully")
        return True

    except subprocess.CalledProcessError as e:
        print(f"\n✗ {scripts} failed with exit code {e.returncode}")
        return False

    except FileNotFoundError:
        print(f"\n✗ Error: {scripts} not found")
        return False

    except Exception as e:
        print(f"\n✗ Unexpected error running {scripts}: {e}")
        return False


def main():
    # Determine the correct path to load-matches.sh based on current working directory
    current_dir = Path.cwd()
    script_dir = Path(__file__).parent

    # Check if we're in the root directory or scripts directory
    if (current_dir / "scripts").exists() and (current_dir / "api").exists():
        # Running from root directory
        load_matches_path = str(current_dir / "api" / "load-matches.sh")
    elif script_dir.parent.name == "overmatch" or (script_dir.parent / "api").exists():
        # Running from scripts directory
        load_matches_path = str(script_dir.parent / "api" / "load-matches.sh")
    else:
        # Fallback to relative path
        load_matches_path = "../api/load-matches.sh"

    # Define the scripts to run in order
    scripts = [
        ["uv", "run", "-m", "scripts.get_osm_ids"],
        ["uv", "run", "-m", "scripts.build_query"],
        ["uv", "run", "-m", "scripts.match"],
        [load_matches_path],
    ]

    print("Starting sequential script execution...")

    for i, script in enumerate(scripts, 1):
        if not run_script(script):
            print(f"\n{'=' * 60}")
            print(f"Execution stopped at script {i}/{len(scripts)}")
            print(f"Failed script: {' '.join(script)}")
            print("=" * 60)
            sys.exit(1)

    print(f"\n{'=' * 60}")
    print("All scripts completed successfully!")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()
