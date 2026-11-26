"""Download the two OpenCV face models (run once, e.g. in Render's build command).

    pip install -r requirements.txt && python scripts/download_face_models.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lite_face  # noqa: E402

if __name__ == "__main__":
    paths = lite_face.ensure_models()
    for name, path in paths.items():
        print(f"OK {name}: {os.path.getsize(path) / 1e6:.1f} MB")
