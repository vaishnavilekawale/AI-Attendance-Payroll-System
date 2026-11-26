"""
prepare_deepface_weights.py  (kept only so old instructions still work)
=======================================================================
The project no longer uses DeepFace/TensorFlow. Face recognition now runs on
two small OpenCV ONNX models (YuNet + SFace) stored in ./face_models.

This script simply downloads them - same as:
    python scripts/download_face_models.py
You can delete this file.
"""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "download_face_models.py"), run_name="__main__")
