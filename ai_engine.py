import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'  # Suppress TensorFlow warnings

import pickle
import time
import logging
import threading
from datetime import datetime
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import cv2
except ImportError:
    cv2 = None

import numpy as np

try:
    import mediapipe as mp
except ImportError:
    mp = None

try:
    from deepface import DeepFace
except ImportError:
    DeepFace = None

from config import Config

logger = logging.getLogger(__name__)

# Global cache for employee face embeddings to avoid repeated disk I/O.
# Each value is a dict: {'embedding': [...], 'mtime': float, 'employee_id': str}
_employee_embeddings_cache = {}

# Guards _employee_embeddings_cache since preload_employee_embeddings() and
# train_employee() can write to it concurrently from multiple threads.
_embeddings_cache_lock = threading.Lock()

# Consistent parameters across all methods
DETECTOR_BACKEND = 'retinaface'
MODEL_NAME = 'Facenet512'

# ---------------------------------------------------------------------------
# STRICT MATCHING CONFIGURATION
# ---------------------------------------------------------------------------
# These constants enforce a "zero false positives" policy for unattended,
# automatic (no-emp_id) recognition. They are intentionally tighter than the
# generic 0.6 default that used to be the sole fallback.
#
# - Facenet512 + cosine distance: published/typical verified-match threshold
#   is ~0.30. We use that as a hard ceiling that the admin-configurable
#   Settings.face_recognition_tolerance can only tighten, never loosen.
# - MIN_MATCH_MARGIN guards against ambiguous matches (e.g. look-alikes):
#   the best-matching employee must beat the second-best candidate by at
#   least this much cosine distance, otherwise the face is reported Unknown
#   instead of guessing.
STRICT_MAX_TOLERANCE = 0.30
MIN_MATCH_MARGIN = 0.05

# Distance below which we treat a single image comparison as a "certain"
# match and can stop scanning further images for that employee.
EARLY_EXIT_DISTANCE = 0.18


# Where the computed embeddings are persisted between server restarts.
EMBEDDINGS_CACHE_FILE = os.path.join(Config.TRAINED_MODEL_FOLDER, 'embeddings_cache.pkl')


def clear_embeddings_cache():
    """Clear the employee embeddings cache - call when employees are added/removed"""
    global _employee_embeddings_cache
    with _embeddings_cache_lock:
        _employee_embeddings_cache.clear()
    logger.info("Employee embeddings cache cleared")


def _load_persistent_embeddings_cache():
    """
    Load previously computed embeddings from disk into memory so the
    server does NOT need to re-encode every employee photo after every
    restart - only new/changed images get (re)encoded.
    """
    global _employee_embeddings_cache
    if not os.path.exists(EMBEDDINGS_CACHE_FILE):
        return
    try:
        with open(EMBEDDINGS_CACHE_FILE, 'rb') as f:
            data = pickle.load(f)
        with _embeddings_cache_lock:
            _employee_embeddings_cache.update(data)
        logger.info(f"Loaded {len(data)} cached face embeddings from disk")
    except Exception as e:
        logger.warning(f"Could not load embeddings cache ({e}); starting fresh")


def _save_persistent_embeddings_cache():
    """Persist the in-memory embeddings cache to disk (atomic write)."""
    try:
        os.makedirs(Config.TRAINED_MODEL_FOLDER, exist_ok=True)
        with _embeddings_cache_lock:
            snapshot = dict(_employee_embeddings_cache)
        tmp_path = EMBEDDINGS_CACHE_FILE + '.tmp'
        with open(tmp_path, 'wb') as f:
            pickle.dump(snapshot, f)
        os.replace(tmp_path, EMBEDDINGS_CACHE_FILE)  # atomic on POSIX & Windows
        logger.info(f"Saved {len(snapshot)} embeddings to disk cache")
    except Exception as e:
        logger.error(f"Could not save embeddings cache: {e}")

# def get_employee_images_cached(employee_id):
#     """
#     Cache ONLY image paths.

#     DeepFace.verify() works most reliably with image paths.
#     We cache paths so disk scanning happens only once while
#     preserving the original recognition behaviour.
#     """

#     global _employee_embeddings_cache

#     if employee_id in _employee_embeddings_cache:
#         return _employee_embeddings_cache[employee_id]

#     employee_folder = os.path.join(
#         Config.DATASET_FOLDER,
#         str(employee_id)
#     )

#     if not os.path.exists(employee_folder):
#         _employee_embeddings_cache[employee_id] = []
#         return []

#     employee_images = []

#     for f in sorted(os.listdir(employee_folder)):
#         if f.lower().endswith((".jpg", ".jpeg", ".png")):
#             img_path = os.path.join(employee_folder, f)

#             if os.path.isfile(img_path):
#                 employee_images.append(img_path)

#     _employee_embeddings_cache[employee_id] = employee_images

#     logger.info(
#         f"Cached {len(employee_images)} image paths for employee {employee_id}"
#     )

#     return employee_images

def get_employee_embedding_cached(employee_id, img_path):
    """
    Return the Facenet512 embedding for a single employee image.

    Two-level cache:
      1. In-memory dict  - instant, used for the life of the process.
      2. On-disk pickle  - populated by preload_employee_embeddings() at
         startup, so a fresh embedding only has to be computed the first
         time an image is ever seen (or after it changes on disk).

    A file's mtime is stored alongside its embedding, so replacing an
    employee's photo automatically invalidates the stale cached entry
    instead of silently reusing an out-of-date embedding.
    """
    global _employee_embeddings_cache
    cache_key = f"{employee_id}_{os.path.basename(img_path)}"

    try:
        current_mtime = os.path.getmtime(img_path)
    except OSError:
        current_mtime = None

    with _embeddings_cache_lock:
        cached = _employee_embeddings_cache.get(cache_key)
        if cached is not None and cached.get('mtime') == current_mtime:
            return cached['embedding']

    try:
        embedding_obj = DeepFace.represent(
            img_path=img_path,
            model_name=MODEL_NAME,
            detector_backend="opencv",
            enforce_detection=False
        )
        if embedding_obj:
            embedding = embedding_obj[0]["embedding"]
            with _embeddings_cache_lock:
                _employee_embeddings_cache[cache_key] = {
                    'embedding': embedding,
                    'mtime': current_mtime,
                    'employee_id': str(employee_id),
                }
            return embedding
    except Exception as e:
        logger.error(f"Embedding error for {img_path}: {e}")

    return None


def preload_employee_embeddings(max_workers=8):
    """
    Warm the embeddings cache for every employee image under
    Config.DATASET_FOLDER, encoding images CONCURRENTLY with a
    ThreadPoolExecutor, and persist the result to disk.

    Call this once at Flask startup (see app.py). On the very first run
    every image has to be encoded, so it takes as long as the model needs
    to process every photo - but in parallel instead of one-by-one. On
    every run after that, images that haven't changed are served straight
    from the on-disk cache (near-instant); only brand-new or modified
    photos are actually re-encoded.
    """
    if DeepFace is None:
        logger.warning("DeepFace not installed. Skipping embeddings preload.")
        return {'total': 0, 'already_cached': 0, 'encoded': 0, 'errors': 0, 'seconds': 0.0}

    start = time.time()

    # 1) Load whatever was cached on disk from previous runs.
    _load_persistent_embeddings_cache()

    # 2) Discover every (employee_id, image_path) pair on disk.
    jobs = []
    if os.path.exists(Config.DATASET_FOLDER):
        for employee_folder in sorted(os.listdir(Config.DATASET_FOLDER)):
            employee_path = os.path.join(Config.DATASET_FOLDER, employee_folder)
            if not os.path.isdir(employee_path):
                continue
            for fname in sorted(os.listdir(employee_path)):
                if fname.lower().endswith(('.jpg', '.jpeg', '.png')):
                    jobs.append((employee_folder, os.path.join(employee_path, fname)))

    total = len(jobs)
    if total == 0:
        logger.info("No employee images found to preload.")
        return {'total': 0, 'already_cached': 0, 'encoded': 0, 'errors': 0, 'seconds': 0.0}

    # 3) Figure out how many are already fresh, purely for reporting.
    already_cached = 0
    for employee_id, img_path in jobs:
        cache_key = f"{employee_id}_{os.path.basename(img_path)}"
        try:
            mtime = os.path.getmtime(img_path)
        except OSError:
            mtime = None
        with _embeddings_cache_lock:
            cached = _employee_embeddings_cache.get(cache_key)
        if cached is not None and cached.get('mtime') == mtime:
            already_cached += 1

    encoded = 0
    errors = 0

    # 4) Encode everything that still needs it, in parallel. DeepFace's
    #    underlying TF/ONNX inference and image decoding release the GIL
    #    for most of their work, so a thread pool gives a real wall-clock
    #    speedup here without the cost of loading a separate model per
    #    worker process (as a ProcessPoolExecutor would require).
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_job = {
            executor.submit(get_employee_embedding_cached, emp_id, img_path): (emp_id, img_path)
            for emp_id, img_path in jobs
        }
        for future in as_completed(future_to_job):
            emp_id, img_path = future_to_job[future]
            try:
                result = future.result()
                if result is not None:
                    encoded += 1
                else:
                    errors += 1
                    logger.warning(f"No embedding produced for {img_path}")
            except Exception as e:
                errors += 1
                logger.error(f"Failed to preload embedding for {img_path}: {e}")

    # 5) Persist everything to disk so the NEXT restart is instant too.
    _save_persistent_embeddings_cache()

    elapsed = time.time() - start
    logger.info(
        f"Embeddings preload complete: {total} images "
        f"({already_cached} already cached, {encoded} newly encoded, "
        f"{errors} errors) in {elapsed:.2f}s using {max_workers} workers"
    )
    return {
        'total': total,
        'already_cached': already_cached,
        'encoded': encoded,
        'errors': errors,
        'seconds': round(elapsed, 2),
    }


def get_recognition_tolerance():
    """
    Load recognition tolerance from Settings, fallback to 0.6.

    The admin-configured value is always hard-capped at STRICT_MAX_TOLERANCE
    so that a looser Settings value can never re-introduce false positives
    into the automatic, no-emp_id recognition flow. Admins can only make
    matching stricter (a smaller number), never looser than the ceiling.
    """
    configured = 0.6
    try:
        from models import Settings
        settings = Settings.get_settings()
        if settings and hasattr(settings, 'face_recognition_tolerance'):
            configured = settings.face_recognition_tolerance
    except Exception as e:
        logger.warning(f"Error loading recognition tolerance from settings: {e}")
    return min(configured, STRICT_MAX_TOLERANCE)


class AttendancePresenceTracker:
    """
    Server-side state machine that tracks which employees are currently
    visible in the camera frame, so that a continuous presence results in
    EXACTLY ONE attendance log - no matter how many recognition frames are
    processed while the person stands in front of the camera.

    State per employee_id:
        AWAY    -> face detected -> log attendance, become PRESENT
        PRESENT -> face detected again -> stays PRESENT, NOT logged again
        PRESENT -> not detected for PRESENCE_TIMEOUT_SECONDS -> becomes AWAY

    This lives on the server (rather than only in browser JS) so the rule
    holds even across multiple browser tabs/kiosks or if the page reloads,
    and can't be bypassed by manipulating client-side state.
    """

    # How long an employee may be briefly out of frame (e.g. turned their
    # head, walked half a step) before we consider them to have actually
    # left. Must be comfortably longer than the scan interval used by the
    # frontend (a few seconds) but short enough that a genuine re-entry is
    # still treated as a new presence.
    PRESENCE_TIMEOUT_SECONDS = 8

    def __init__(self):
        self._lock = threading.Lock()
        self._last_seen = {}   # employee_id -> monotonic timestamp
        self._present = set()  # employee_ids currently considered "in frame"

    def register_detection(self, employee_id):
        """
        Call once per scan for every employee_id recognized in the current
        frame. Returns True the first time this call represents a NEW
        presence (i.e. attendance should be logged now), and False if the
        employee is already known to be present (attendance already logged
        for this presence - do not log again).
        """
        employee_id = str(employee_id)
        now = time.monotonic()
        with self._lock:
            last_seen = self._last_seen.get(employee_id)
            has_left_and_returned = (
                last_seen is not None
                and (now - last_seen) > self.PRESENCE_TIMEOUT_SECONDS
            )
            is_new_presence = (
                employee_id not in self._present or has_left_and_returned
            )

            self._last_seen[employee_id] = now
            self._present.add(employee_id)

            return is_new_presence

    def sweep(self):
        """Drop employees who have not been seen for a while so state
        doesn't grow unbounded, and so their next detection is correctly
        treated as a fresh presence."""
        now = time.monotonic()
        with self._lock:
            stale = [
                emp for emp, last in self._last_seen.items()
                if (now - last) > self.PRESENCE_TIMEOUT_SECONDS
            ]
            for emp in stale:
                self._last_seen.pop(emp, None)
                self._present.discard(emp)

    def reset(self, employee_id=None):
        """Manually clear presence state (e.g. for tests or admin override).
        With no argument, clears everyone."""
        with self._lock:
            if employee_id is None:
                self._last_seen.clear()
                self._present.clear()
            else:
                employee_id = str(employee_id)
                self._last_seen.pop(employee_id, None)
                self._present.discard(employee_id)


# Single shared instance used by the real-time auto-scan endpoint.
presence_tracker = AttendancePresenceTracker()


class FaceDetectionEngine:
    def __init__(self):
        self.use_deepface = DeepFace is not None
        if mp is None and not self.use_deepface:
            logger.warning("MediaPipe and DeepFace not installed. Face detection will not work.")
            self.face_detection = None
            return

        if mp is not None:
            self.mp_face_detection = mp.solutions.face_detection
            self.mp_drawing = mp.solutions.drawing_utils
            self.face_detection = self.mp_face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=0.5
            )
        else:
            self.face_detection = None

    def detect_face(self, frame):
        """Detect faces in frame using DeepFace or MediaPipe"""
        if cv2 is None:
            logger.warning("OpenCV not installed. Face detection will not work.")
            return []

        # Prefer DeepFace if available
        if self.use_deepface:
            return self._detect_with_deepface(frame)

        # Fallback to MediaPipe
        if self.face_detection is None:
            logger.warning("Face detection not available.")
            return []
        return self._detect_with_mediapipe(frame)

    def _detect_with_deepface(self, frame):
        """Detect faces using DeepFace"""
        temp_path = os.path.join(Config.TRAINED_MODEL_FOLDER, 'temp_detect.jpg')
        try:
            os.makedirs(Config.TRAINED_MODEL_FOLDER, exist_ok=True)
            resized_frame = cv2.resize(frame, (640, 640))
            
            # ✅ Fix 1 & 2: Save frame before passing to DeepFace
            cv2.imwrite(temp_path, resized_frame)

            # ✅ Fix 4: Use consistent detector_backend
            faces = DeepFace.extract_faces(
                img_path=temp_path,
                detector_backend=DETECTOR_BACKEND,
                enforce_detection=False
            )

            result = []
            for face in faces:
                facial_area = face['facial_area']
                result.append({
                    'bbox': (facial_area['x'], facial_area['y'],
                             facial_area['w'], facial_area['h']),
                    'confidence': face.get('confidence', 0.0)
                })

            return result
        except Exception as e:
            logger.error(f"DeepFace detection error: {e}")
            if self.face_detection is not None:
                return self._detect_with_mediapipe(frame)
            return []
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _detect_with_mediapipe(self, frame):
        """Detect faces using MediaPipe"""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.face_detection.process(rgb_frame)

        faces = []
        if results.detections:
            h, w, _ = frame.shape
            for detection in results.detections:
                bbox = detection.location_data.relative_bounding_box
                x = int(bbox.xmin * w)
                y = int(bbox.ymin * h)
                width = int(bbox.width * w)
                height = int(bbox.height * h)

                faces.append({
                    'bbox': (x, y, width, height),
                    'confidence': float(detection.score[0])
                })

        return faces

    def draw_faces(self, frame, faces):
        """Draw bounding boxes around detected faces"""
        if cv2 is None:
            return frame
        for face in faces:
            x, y, w, h = face['bbox']
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(frame, f"Conf: {face['confidence']:.2f}",
                        (x, max(10, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        return frame

    def __del__(self):
        if hasattr(self, 'face_detection') and self.face_detection:
            self.face_detection.close()


class FaceRecognitionEngine:
    def __init__(self):
        self.known_face_ids = []
        self.known_face_names = []
        self.model_path = os.path.join(Config.TRAINED_MODEL_FOLDER, 'face_data.pkl')
        self.load_model()

    def load_model(self):
        """Load trained face recognition data"""
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, 'rb') as f:
                    data = pickle.load(f)
                    self.known_face_ids = data.get('ids', [])
                    self.known_face_names = data.get('names', [])
                logger.info(f"Loaded face recognition data with {len(self.known_face_ids)} employees")
            except Exception as e:
                logger.error(f"Error loading model: {e}")
                logger.info("Deleting corrupted model file and starting fresh")
                try:
                    os.remove(self.model_path)
                except:
                    pass
                self.known_face_ids = []
                self.known_face_names = []

    def save_model(self):
        """Save trained face recognition data"""
        os.makedirs(Config.TRAINED_MODEL_FOLDER, exist_ok=True)
        data = {
            'ids': self.known_face_ids,
            'names': self.known_face_names
        }
        with open(self.model_path, 'wb') as f:
            pickle.dump(data, f)

        logger.info(f"Model saved with {len(self.known_face_ids)} employees")

    def train_employee(self, employee_id, employee_name, image_paths):
        """Register employee face images and clear cache for this employee"""
        if DeepFace is None:
            logger.warning("DeepFace not installed. Training will not work.")
            return 0

        valid_images = 0

        for img_path in image_paths:
            try:
                # Check image exists
                if not os.path.exists(img_path):
                    logger.warning(f"Image not found: {img_path}")
                    continue

                # Read image with OpenCV
                image = cv2.imread(img_path)

                if image is None:
                    logger.warning(f"Could not read image: {img_path}")
                    continue

                # Detect face
                faces = DeepFace.extract_faces(
                    img_path=img_path,
                    detector_backend="opencv",
                    enforce_detection=False
                )

                if faces and len(faces) > 0:
                    valid_images += 1
                    logger.debug(f"Valid face detected in {img_path}")
                else:
                    logger.warning(f"No face detected in {img_path}")

            except Exception as e:
                logger.debug(f"Error processing {img_path}: {e}")

        if valid_images > 0:
            if employee_id not in self.known_face_ids:
                self.known_face_ids.append(employee_id)
                self.known_face_names.append(employee_name)

            self.save_model()

            # Drop any stale cached embeddings for this employee (in case
            # a photo was replaced) and warm the cache for their current
            # images in parallel, then persist to disk.
            global _employee_embeddings_cache
            prefix = f"{employee_id}_"
            with _embeddings_cache_lock:
                stale_keys = [k for k in _employee_embeddings_cache if k.startswith(prefix)]
                for k in stale_keys:
                    del _employee_embeddings_cache[k]

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(
                    lambda p: get_employee_embedding_cached(employee_id, p),
                    image_paths
                ))
            _save_persistent_embeddings_cache()

            logger.info(f"Registered employee {employee_name} with {valid_images} valid images")
            return valid_images

        logger.warning("No valid images found for training.")
        return 0


    def recognize_face(self, frame, tolerance=None, target_employee_id=None):
        """
        Recognize every face present in the frame (multi-person aware).

        Returns a list with ONE result per detected face:
            {"name", "employee_id", "confidence", "bbox"}
        Unrecognized / low-confidence / ambiguous faces are returned as
        {"name": "Unknown", "employee_id": None, ...} rather than being
        dropped, so callers can still see "a face is there, just not
        matched" and multi-person frames are handled cleanly.

        Strict-matching rules (zero false positives):
          - distance must be below the (hard-capped) tolerance
          - the best match must beat the runner-up candidate by at least
            MIN_MATCH_MARGIN, otherwise it's ambiguous -> Unknown
        """
        start_time = time.time()

        if tolerance is None:
            tolerance = get_recognition_tolerance()
        else:
            # Even an explicitly-passed tolerance can never exceed the
            # strict ceiling - callers cannot loosen matching below the
            # zero-false-positive floor.
            tolerance = min(tolerance, STRICT_MAX_TOLERANCE)

        if DeepFace is None:
            logger.warning("DeepFace not installed.")
            return []

        if len(self.known_face_ids) == 0:
            logger.warning("No trained employees available.")
            return []

        try:
            resized_frame = cv2.resize(frame, (640, 480))

            # DeepFace.represent() detects and embeds EVERY face found in
            # the frame - not just the first one - which is what lets us
            # analyze multi-person frames.
            target_objs = DeepFace.represent(
                img_path=resized_frame,
                model_name=MODEL_NAME,
                detector_backend="opencv",
                enforce_detection=False
            )
        except Exception as e:
            logger.debug(f"Target feature extraction error: {e}")
            return []

        if not target_objs:
            return []

        if target_employee_id:
            if target_employee_id in self.known_face_ids:
                employees_to_check = [target_employee_id]
            else:
                logger.warning(f"Employee not found in list: {target_employee_id}")
                return []
        else:
            employees_to_check = self.known_face_ids

        frame_area = 640 * 480
        results = []

        for face_obj in target_objs:
            facial_area = face_obj.get("facial_area", {})
            bbox = (
                facial_area.get("x", 0), facial_area.get("y", 0),
                facial_area.get("w", 0), facial_area.get("h", 0)
            )

            bbox_area = bbox[2] * bbox[3]
            if bbox_area <= 0 or bbox_area > 0.8 * frame_area:
                # Degenerate detection (covers the whole frame, or zero
                # area) - not a real face, skip this candidate silently.
                continue

            target_embedding = np.array(face_obj["embedding"])

            best_match_id = None
            min_distance = float("inf")
            second_min_distance = float("inf")
            stop_scanning = False

            for emp_id in employees_to_check:
                emp_folder = os.path.join(Config.DATASET_FOLDER, str(emp_id))
                if not os.path.exists(emp_folder):
                    continue

                emp_best_distance = float("inf")
                for f in os.listdir(emp_folder):
                    if not f.lower().endswith(('.jpg', '.jpeg', '.png')):
                        continue

                    img_path = os.path.join(emp_folder, f)
                    cached_emb = get_employee_embedding_cached(emp_id, img_path)
                    if cached_emb is None:
                        continue

                    cached_emb = np.array(cached_emb)
                    distance = 1.0 - (
                        np.dot(target_embedding, cached_emb) /
                        (np.linalg.norm(target_embedding) * np.linalg.norm(cached_emb))
                    )

                    if distance < emp_best_distance:
                        emp_best_distance = distance

                    if distance < EARLY_EXIT_DISTANCE:
                        # Certain match against this employee's photo -
                        # no need to compare the rest of their images.
                        break

                if emp_best_distance < min_distance:
                    second_min_distance = min_distance
                    min_distance = emp_best_distance
                    best_match_id = emp_id
                elif emp_best_distance < second_min_distance:
                    second_min_distance = emp_best_distance

                if min_distance < EARLY_EXIT_DISTANCE:
                    # Overwhelmingly confident match - safe to stop
                    # checking the remaining employees for this face.
                    stop_scanning = True
                    break

                if stop_scanning:
                    break

            name = "Unknown"
            matched_employee_id = None
            confidence = 0.0

            has_clear_margin = (
                second_min_distance == float("inf")
                or (second_min_distance - min_distance) >= MIN_MATCH_MARGIN
            )

            if best_match_id is not None and min_distance < tolerance and has_clear_margin:
                idx = self.known_face_ids.index(best_match_id)
                name = self.known_face_names[idx]
                matched_employee_id = best_match_id
                confidence = max(0.0, min(1.0, 1.0 - min_distance))
                logger.info(
                    f"Face recognized: {name} (ID: {matched_employee_id}), "
                    f"confidence: {confidence:.2f}, distance: {min_distance:.3f}"
                )
            else:
                logger.info(
                    f"Face recognition: no confident/unambiguous match "
                    f"(best distance: {min_distance:.3f}, margin ok: {has_clear_margin})"
                )

            results.append({
                "name": name,
                "employee_id": matched_employee_id,
                "confidence": round(confidence, 2),
                "bbox": bbox
            })

        logger.debug(
            f"recognize_face processed {len(results)} face(s) in "
            f"{time.time() - start_time:.2f}s"
        )
        return results

  
    #     return results
    def remove_employee(self, employee_id):
        """Remove employee from face recognition model and clear cache"""
        if employee_id in self.known_face_ids:
            index = self.known_face_ids.index(employee_id)
            del self.known_face_ids[index]
            del self.known_face_names[index]

            employee_folder = os.path.join(Config.DATASET_FOLDER, str(employee_id))
            if os.path.exists(employee_folder):
                import shutil
                shutil.rmtree(employee_folder)

            # Clear all cached embeddings for this employee (keys are
            # "{employee_id}_{filename}", not the bare employee_id).
            global _employee_embeddings_cache
            prefix = f"{employee_id}_"
            with _embeddings_cache_lock:
                stale_keys = [k for k in _employee_embeddings_cache if k.startswith(prefix)]
                for k in stale_keys:
                    del _employee_embeddings_cache[k]
            _save_persistent_embeddings_cache()

            self.save_model()
            return 1
        return 0


class FaceCapture:
    def __init__(self, employee_id, num_images=20):
        self.employee_id = employee_id
        self.num_images = num_images
        self.captured_count = 0
        self.cap = None
        self.detector = FaceDetectionEngine()
        self.save_dir = os.path.join(Config.DATASET_FOLDER, str(employee_id))
        os.makedirs(self.save_dir, exist_ok=True)

    def start_capture(self):
        """Start webcam capture with 640x480 resolution for faster processing"""
        if cv2 is None:
            raise Exception("OpenCV not installed. Cannot capture from webcam.")
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            raise Exception("Could not open webcam")
        
        # Set camera resolution to 640x480 for faster processing (as requested)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        return self.cap

    def capture_frame(self):
        """Capture frame and save cropped face for better accuracy"""
        if cv2 is None:
            return None, False
        ret, frame = self.cap.read()
        if not ret:
            return None, False

        faces = self.detector.detect_face(frame)
        drawn_frame = self.detector.draw_faces(frame.copy(), faces)

        # ✅ Fix 7: Save Cropped Face instead of full frame
        if len(faces) > 0 and self.captured_count < self.num_images:
            x, y, w, h = faces[0]['bbox']
            
            # Boundary check
            h_img, w_img, _ = frame.shape
            x, y = max(0, x), max(0, y)
            w, h = min(w_img - x, w), min(h_img - y, h)

            if w > 0 and h > 0:
                face_crop = frame[y:y+h, x:x+w]
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                img_path = os.path.join(self.save_dir, f"{timestamp}.jpg")
                cv2.imwrite(img_path, face_crop)
                self.captured_count += 1
                return drawn_frame, True

        return drawn_frame, False

    def stop_capture(self):
        """Stop webcam capture"""
        if self.cap:
            self.cap.release()
        if cv2 is not None:
            cv2.destroyAllWindows()

    def get_captured_images(self):
        """Get list of captured images"""
        if os.path.exists(self.save_dir):
            return [os.path.join(self.save_dir, f) for f in os.listdir(self.save_dir)
                    if f.endswith(('.jpg', '.jpeg', '.png'))]
        return []


def train_all_employees():
    """Train face recognition for all employees with face images"""
    if cv2 is None:
        logger.warning("OpenCV not installed. Training will not work.")
        return
    recognizer = FaceRecognitionEngine()

    if not os.path.exists(Config.DATASET_FOLDER):
        logger.warning("Dataset folder does not exist")
        return

    for employee_folder in os.listdir(Config.DATASET_FOLDER):
        employee_path = os.path.join(Config.DATASET_FOLDER, employee_folder)
        if os.path.isdir(employee_path):
            employee_id = employee_folder
            image_paths = [os.path.join(employee_path, f) for f in os.listdir(employee_path)
                           if f.endswith(('.jpg', '.jpeg', '.png'))]

            if len(image_paths) >= getattr(Config, 'MIN_FACE_IMAGES_REQUIRED', 1):
                from models import Employee
                employee = Employee.query.filter_by(id=int(employee_id)).first()
                if employee:
                    recognizer.train_employee(employee_id, employee.name, image_paths)
                    logger.info(f"Trained employee {employee.name} with {len(image_paths)} images")
  