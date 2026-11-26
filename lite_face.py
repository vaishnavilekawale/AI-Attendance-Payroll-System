"""
lite_face.py - a tiny, TensorFlow-free replacement for the parts of DeepFace
that ai_engine.py uses (``DeepFace.represent`` and ``DeepFace.extract_faces``).

Why
---
DeepFace pulls in TensorFlow (+ RetinaFace / Facenet512 weights). Importing
TensorFlow alone needs 300-400 MB of RAM, which is more than Render's free
plan (512 MB) can give you, so the instance was killed right after training.

This module uses OpenCV's built-in ONNX face models instead:

    * YuNet  (face_detection_yunet_2023mar.onnx)   ~0.2 MB  - face detector
    * SFace  (face_recognition_sface_2021dec.onnx) ~37 MB   - 128-d embeddings

Total RAM is roughly 100-150 MB. No TensorFlow, no Keras, no mediapipe.

The two functions return the same structure DeepFace does, so ai_engine.py
only needs its import line changed:

    represent(...)      -> [{"embedding": [128 floats],
                             "facial_area": {"x","y","w","h"},
                             "face_confidence": float}, ...]
    extract_faces(...)  -> [{"face": ndarray, "facial_area": {...},
                             "confidence": float}, ...]

Model files
-----------
They are looked up in $LITE_FACE_MODEL_DIR (default: <project>/face_models) and
downloaded automatically on first use if missing. To avoid the download at
runtime, run ``python scripts/download_face_models.py`` in the Render build
command, or commit the two .onnx files to that folder.
"""
import logging
import os
import threading
import urllib.request

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.environ.get("LITE_FACE_MODEL_DIR") or os.path.join(_BASE_DIR, "face_models")

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"

_ZOO = "opencv/opencv_zoo/main/models"
_MODEL_URLS = {
    DETECTOR_FILE: [
        f"https://media.githubusercontent.com/media/{_ZOO}/face_detection_yunet/{DETECTOR_FILE}",
        f"https://github.com/{_ZOO.replace('/main', '/raw/main')}/face_detection_yunet/{DETECTOR_FILE}",
    ],
    RECOGNIZER_FILE: [
        f"https://media.githubusercontent.com/media/{_ZOO}/face_recognition_sface/{RECOGNIZER_FILE}",
        f"https://github.com/{_ZOO.replace('/main', '/raw/main')}/face_recognition_sface/{RECOGNIZER_FILE}",
    ],
}
# A Git-LFS "pointer" file is only a few hundred bytes - reject anything tiny.
_MIN_BYTES = {DETECTOR_FILE: 100_000, RECOGNIZER_FILE: 10_000_000}

# Faces smaller than this (pixels, either side) are ignored as noise.
MIN_FACE_SIZE = int(os.environ.get("LITE_FACE_MIN_SIZE", "40"))
SCORE_THRESHOLD = float(os.environ.get("LITE_FACE_SCORE", "0.8"))
# Longest image side fed to the detector (bigger images are scaled down).
MAX_DETECT_SIDE = int(os.environ.get("LITE_FACE_MAX_SIDE", "640"))
# Small images (e.g. stored face crops) with no detectable face are used as a
# whole "face" when enforce_detection=False, like DeepFace did.
SMALL_IMAGE_SIDE = 400

_lock = threading.RLock()
_detector = None
_recognizer = None


def ensure_models():
    """Make sure both ONNX files exist locally (download if missing)."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    paths = {}
    for name, urls in _MODEL_URLS.items():
        path = os.path.join(MODEL_DIR, name)
        if os.path.exists(path) and os.path.getsize(path) >= _MIN_BYTES[name]:
            paths[name] = path
            continue
        last_err = None
        for url in urls:
            try:
                logger.info("Downloading face model %s ...", name)
                tmp = path + ".part"
                req = urllib.request.Request(url, headers={"User-Agent": "aiaps"})
                with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                if os.path.getsize(tmp) < _MIN_BYTES[name]:
                    raise RuntimeError("downloaded file too small (LFS pointer?)")
                os.replace(tmp, path)
                last_err = None
                break
            except Exception as exc:  # try next URL
                last_err = exc
                logger.warning("Download from %s failed: %s", url, exc)
        if last_err is not None:
            raise RuntimeError(f"Could not obtain face model {name}: {last_err}")
        paths[name] = path
    return paths


def _get_models():
    global _detector, _recognizer
    with _lock:
        if _detector is None or _recognizer is None:
            paths = ensure_models()
            _detector = cv2.FaceDetectorYN.create(
                paths[DETECTOR_FILE], "", (320, 320), SCORE_THRESHOLD, 0.3, 5000
            )
            _recognizer = cv2.FaceRecognizerSF.create(paths[RECOGNIZER_FILE], "")
            logger.info("Lite face models loaded (YuNet + SFace)")
    return _detector, _recognizer


def _to_bgr(img):
    """Accept an ndarray or a file path and return a 3-channel BGR image."""
    if isinstance(img, (str, os.PathLike)):
        arr = cv2.imread(str(img), cv2.IMREAD_COLOR)
        if arr is None:
            raise ValueError(f"Could not read image: {img}")
        return arr
    arr = np.asarray(img)
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    return arr


def _detect_rows(image):
    """Run YuNet. Returns an (N, 15) float32 array in ``image`` coordinates."""
    detector, _ = _get_models()
    h, w = image.shape[:2]
    scale = 1.0
    work = image
    if max(h, w) > MAX_DETECT_SIDE:
        scale = MAX_DETECT_SIDE / float(max(h, w))
        work = cv2.resize(image, (int(round(w * scale)), int(round(h * scale))))
    wh, ww = work.shape[:2]
    detector.setInputSize((ww, wh))
    _, faces = detector.detect(work)
    if faces is None or len(faces) == 0:
        return np.zeros((0, 15), dtype=np.float32)
    faces = faces.astype(np.float32).copy()
    if scale != 1.0:
        faces[:, :14] /= scale
    return faces


def _find_faces(image):
    """
    Detect faces. Returns (rows, work_image, pad): the detections are in the
    coordinates of ``work_image`` which is ``image`` itself, or - for small face
    crops where nothing was found - a copy padded by ``pad`` pixels per side.
    """
    faces = _detect_rows(image)
    work, pad = image, 0
    if len(faces) == 0 and max(image.shape[:2]) <= SMALL_IMAGE_SIDE:
        pad = int(0.4 * max(image.shape[:2]))
        work = cv2.copyMakeBorder(image, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        faces = _detect_rows(work)
        if len(faces) == 0:
            work, pad = image, 0
    small = max(image.shape[:2]) <= SMALL_IMAGE_SIDE
    keep = [f for f in faces
            if small or (f[2] >= MIN_FACE_SIZE and f[3] >= MIN_FACE_SIZE)]
    return keep, work, pad


def _area(row, shape, pad=0):
    h, w = shape[:2]
    x = int(max(0, round(row[0] - pad)))
    y = int(max(0, round(row[1] - pad)))
    fw = int(min(w - x, round(row[2])))
    fh = int(min(h - y, round(row[3])))
    return {"x": x, "y": y, "w": max(fw, 0), "h": max(fh, 0)}


def _whole_image_face(image):
    h, w = image.shape[:2]
    return {"x": 0, "y": 0, "w": w, "h": h}


def represent(img_path, model_name=None, detector_backend=None,
              enforce_detection=True, **_ignored):
    """Embed every face in the image (DeepFace.represent compatible)."""
    image = _to_bgr(img_path)
    results = []
    with _lock:
        _, recognizer = _get_models()
        rows, work, pad = _find_faces(image)
        if rows:
            for row in rows:
                try:
                    aligned = recognizer.alignCrop(work, row[:15].reshape(1, 15))
                    feat = recognizer.feature(aligned).flatten()
                except cv2.error as exc:
                    logger.debug("SFace failed on one face: %s", exc)
                    continue
                results.append({
                    "embedding": [float(v) for v in feat],
                    "facial_area": _area(row, image.shape, pad),
                    "face_confidence": float(row[14]),
                })
        elif not enforce_detection and max(image.shape[:2]) <= SMALL_IMAGE_SIDE:
            # Stored face crop where no face could be located: embed it whole.
            aligned = cv2.resize(image, (112, 112))
            feat = recognizer.feature(aligned).flatten()
            results.append({
                "embedding": [float(v) for v in feat],
                "facial_area": _whole_image_face(image),
                "face_confidence": 0.0,
            })
    if not results and enforce_detection:
        raise ValueError("Face could not be detected.")
    return results


def extract_faces(img_path, detector_backend=None, enforce_detection=True,
                  **_ignored):
    """Detect faces (DeepFace.extract_faces compatible)."""
    image = _to_bgr(img_path)
    out = []
    with _lock:
        rows, _work, pad = _find_faces(image)
        for row in rows:
            area = _area(row, image.shape, pad)
            if area["w"] <= 0 or area["h"] <= 0:
                continue
            crop = image[area["y"]:area["y"] + area["h"],
                         area["x"]:area["x"] + area["w"]]
            out.append({
                "face": crop,
                "facial_area": area,
                "confidence": float(row[14]),
            })
        if not out and not enforce_detection and max(image.shape[:2]) <= SMALL_IMAGE_SIDE:
            out.append({
                "face": image,
                "facial_area": _whole_image_face(image),
                "confidence": 0.0,
            })
    if not out and enforce_detection:
        raise ValueError("Face could not be detected.")
    return out


class _LiteDeepFace:
    """Namespace so ``DeepFace.represent(...)`` keeps working unchanged."""
    represent = staticmethod(represent)
    extract_faces = staticmethod(extract_faces)


DeepFace = _LiteDeepFace()
