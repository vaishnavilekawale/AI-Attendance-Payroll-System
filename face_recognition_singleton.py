"""
Global face-recognition-engine singleton, extracted out of app.py so that
blueprints (e.g. blueprints/employees.py, for its delete route which
removes an employee from the recognizer) can use it without a circular
import - the same reasoning as extensions.py / auth_decorators.py /
auth_helpers.py.

FaceRecognitionEngine itself (ai_engine.py) has no Flask dependency, so
this module doesn't need an app instance at all - it's a plain lazy
singleton.
"""
from ai_engine import FaceRecognitionEngine

_face_recognizer = None


def get_face_recognizer():
    """Get or create the global face recognition engine instance."""
    global _face_recognizer
    if _face_recognizer is None:
        _face_recognizer = FaceRecognitionEngine()
    return _face_recognizer
