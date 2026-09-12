"""
Tests for the "face matching" layer of ai_engine.py: FaceRecognitionEngine
(training, model persistence, recognize_face's strict-matching logic,
employee removal) plus its auto-reload/rescan helpers.

As in test_ai_engine_dataset_mapping.py, cv2/DeepFace are the globally
stubbed modules from conftest.py; individual tests monkeypatch
ai_engine.DeepFace / ai_engine.cv2 attributes for fine-grained control
over "detected face" / "computed embedding" behaviour.
"""
import os
import pickle
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

import ai_engine
from ai_engine import FaceRecognitionEngine, STRICT_MAX_TOLERANCE, MIN_MATCH_MARGIN, EARLY_EXIT_DISTANCE
from config import Config


FAKE_IMAGE = np.zeros((20, 20, 3), dtype=np.uint8)


@pytest.fixture(autouse=True)
def _isolate_embeddings_cache():
    ai_engine.clear_embeddings_cache()
    yield
    ai_engine.clear_embeddings_cache()


@pytest.fixture
def dataset_and_model_dirs(tmp_path, monkeypatch):
    dataset_dir = tmp_path / "dataset"
    model_dir = tmp_path / "trained_model"
    dataset_dir.mkdir()
    model_dir.mkdir()
    monkeypatch.setattr(Config, 'DATASET_FOLDER', str(dataset_dir))
    monkeypatch.setattr(Config, 'TRAINED_MODEL_FOLDER', str(model_dir))
    monkeypatch.setattr(ai_engine, 'EMBEDDINGS_CACHE_FILE', str(model_dir / 'embeddings_cache.pkl'))
    return dataset_dir, model_dir


@pytest.fixture
def engine(dataset_and_model_dirs):
    """A fresh FaceRecognitionEngine with no trained employees, pointed at
    an isolated model_path (no pre-existing face_data.pkl)."""
    return FaceRecognitionEngine()


def _make_employee_photo(dataset_dir, employee_id, filename="a.jpg", content=b'imgbytes'):
    emp_dir = dataset_dir / str(employee_id)
    emp_dir.mkdir(exist_ok=True)
    path = emp_dir / filename
    path.write_bytes(content)
    return str(path)


def _patch_basic_decode(monkeypatch):
    monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)
    monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: FAKE_IMAGE)
    monkeypatch.setattr(ai_engine.cv2, 'resize', lambda frame, size: frame)


# ---------------------------------------------------------------------
# load_model / save_model
# ---------------------------------------------------------------------

class TestModelPersistence:

    def test_new_engine_starts_empty_when_no_model_file(self, engine):
        assert engine.known_face_ids == []
        assert engine.known_face_names == []

    def test_save_then_reload_round_trips_known_faces(self, dataset_and_model_dirs):
        e1 = FaceRecognitionEngine()
        e1.known_face_ids = ['1', '2']
        e1.known_face_names = ['Alice', 'Bob']
        e1.save_model()

        e2 = FaceRecognitionEngine()  # __init__ calls load_model()
        assert e2.known_face_ids == ['1', '2']
        assert e2.known_face_names == ['Alice', 'Bob']

    def test_load_model_deletes_and_recovers_from_corrupted_file(self, dataset_and_model_dirs):
        _, model_dir = dataset_and_model_dirs
        model_path = model_dir / 'face_data.pkl'
        model_path.write_bytes(b'not a real pickle')

        e = FaceRecognitionEngine()

        assert e.known_face_ids == []
        assert e.known_face_names == []
        # Corrupted file must have been removed so a future save() succeeds cleanly.
        assert not model_path.exists()

    def test_save_model_creates_trained_model_folder(self, tmp_path, monkeypatch):
        model_dir = tmp_path / "brand_new"
        monkeypatch.setattr(Config, 'TRAINED_MODEL_FOLDER', str(model_dir))
        monkeypatch.setattr(Config, 'DATASET_FOLDER', str(tmp_path / 'dataset'))
        e = FaceRecognitionEngine()
        e.known_face_ids = ['1']
        e.known_face_names = ['Alice']

        e.save_model()

        assert model_dir.exists()
        assert os.path.exists(e.model_path)


# ---------------------------------------------------------------------
# train_employee
# ---------------------------------------------------------------------

class TestTrainEmployee:

    def test_returns_zero_when_deepface_not_installed(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        img = _make_employee_photo(dataset_dir, '1')
        monkeypatch.setattr(ai_engine, 'DeepFace', None)

        result = engine.train_employee('1', 'Alice', [img])

        assert result == 0
        assert engine.known_face_ids == []

    def test_registers_employee_with_valid_face_images(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        img1 = _make_employee_photo(dataset_dir, '1', 'a.jpg')
        img2 = _make_employee_photo(dataset_dir, '1', 'b.jpg')
        _patch_basic_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.extract_faces.return_value = [{'face': FAKE_IMAGE}]
        fake_deepface.represent.return_value = [{'embedding': [0.1, 0.2]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = engine.train_employee('1', 'Alice', [img1, img2])

        assert result == 2
        assert engine.known_face_ids == ['1']
        assert engine.known_face_names == ['Alice']
        assert os.path.exists(engine.model_path)
        # Embeddings should have been warmed for both images.
        assert ai_engine._employee_embeddings_cache.get('1_a.jpg') is not None
        assert ai_engine._employee_embeddings_cache.get('1_b.jpg') is not None

    def test_does_not_duplicate_already_known_employee(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        img = _make_employee_photo(dataset_dir, '1')
        _patch_basic_decode(monkeypatch)
        fake_deepface = MagicMock()
        fake_deepface.extract_faces.return_value = [{'face': FAKE_IMAGE}]
        fake_deepface.represent.return_value = [{'embedding': [0.1]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']

        engine.train_employee('1', 'Alice', [img])

        assert engine.known_face_ids.count('1') == 1

    def test_skips_missing_image_file(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        missing = str(dataset_dir / '1' / 'missing.jpg')
        (dataset_dir / '1').mkdir(exist_ok=True)
        _patch_basic_decode(monkeypatch)
        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = engine.train_employee('1', 'Alice', [missing])

        assert result == 0
        fake_deepface.extract_faces.assert_not_called()

    def test_skips_image_with_no_detected_face(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        img = _make_employee_photo(dataset_dir, '1')
        _patch_basic_decode(monkeypatch)
        fake_deepface = MagicMock()
        fake_deepface.extract_faces.return_value = []  # no face detected
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = engine.train_employee('1', 'Alice', [img])

        assert result == 0
        assert engine.known_face_ids == []

    def test_continues_after_per_image_exception(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        img1 = _make_employee_photo(dataset_dir, '1', 'a.jpg')
        img2 = _make_employee_photo(dataset_dir, '1', 'b.jpg')
        _patch_basic_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.extract_faces.side_effect = [
            RuntimeError("decrypt failed"),
            [{'face': FAKE_IMAGE}],
        ]
        fake_deepface.represent.return_value = [{'embedding': [0.1]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = engine.train_employee('1', 'Alice', [img1, img2])

        assert result == 1
        assert engine.known_face_ids == ['1']

    def test_returns_zero_and_does_not_register_when_no_valid_images(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        img = _make_employee_photo(dataset_dir, '1')
        _patch_basic_decode(monkeypatch)
        fake_deepface = MagicMock()
        fake_deepface.extract_faces.return_value = []
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = engine.train_employee('1', 'Alice', [img])

        assert result == 0
        assert not os.path.exists(engine.model_path)


# ---------------------------------------------------------------------
# remove_employee
# ---------------------------------------------------------------------

class TestRemoveEmployee:

    def test_removes_known_employee_and_their_dataset_folder(self, engine, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        emp_dir = dataset_dir / '1'
        emp_dir.mkdir()
        (emp_dir / 'a.jpg').write_bytes(b'x')

        engine.known_face_ids = ['1', '2']
        engine.known_face_names = ['Alice', 'Bob']
        ai_engine._employee_embeddings_cache['1_a.jpg'] = {'embedding': [1], 'mtime': 1, 'employee_id': '1'}
        ai_engine._employee_embeddings_cache['2_a.jpg'] = {'embedding': [2], 'mtime': 1, 'employee_id': '2'}

        result = engine.remove_employee('1')

        assert result == 1
        assert engine.known_face_ids == ['2']
        assert engine.known_face_names == ['Bob']
        assert not emp_dir.exists()
        assert '1_a.jpg' not in ai_engine._employee_embeddings_cache
        assert '2_a.jpg' in ai_engine._employee_embeddings_cache

    def test_returns_zero_for_unknown_employee(self, engine):
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']

        result = engine.remove_employee('999')

        assert result == 0
        assert engine.known_face_ids == ['1']

    def test_handles_missing_dataset_folder_gracefully(self, engine, dataset_and_model_dirs):
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']
        # No dataset/1 folder was ever created on disk.
        result = engine.remove_employee('1')
        assert result == 1
        assert engine.known_face_ids == []


# ---------------------------------------------------------------------
# recognize_face
# ---------------------------------------------------------------------

class TestRecognizeFace:

    def test_returns_empty_when_deepface_missing(self, engine, monkeypatch):
        monkeypatch.setattr(ai_engine, 'DeepFace', None)
        assert engine.recognize_face(FAKE_IMAGE) == []

    def test_returns_empty_and_triggers_reload_when_no_known_employees(self, engine, monkeypatch):
        # Run background reload synchronously and deterministically for the
        # assertion, instead of racing a real daemon thread.
        started = threading.Event()

        class ImmediateThread:
            def __init__(self, target=None, daemon=None, name=None):
                self._target = target
            def start(self):
                self._target()
                started.set()

        monkeypatch.setattr(ai_engine.threading, 'Thread', ImmediateThread)
        monkeypatch.setattr(engine, 'load_model', lambda: None)
        monkeypatch.setattr(engine, 'rescan_and_train_untrained_employees', lambda: 0)

        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = engine.recognize_face(FAKE_IMAGE)

        assert result == []
        assert started.is_set()

    def test_returns_empty_when_target_extraction_raises(self, engine, monkeypatch, dataset_and_model_dirs):
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']
        _patch_basic_decode(monkeypatch)
        fake_deepface = MagicMock()
        fake_deepface.represent.side_effect = RuntimeError("boom")
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        assert engine.recognize_face(FAKE_IMAGE) == []

    def test_returns_empty_when_no_faces_detected_in_frame(self, engine, monkeypatch):
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']
        _patch_basic_decode(monkeypatch)
        fake_deepface = MagicMock()
        fake_deepface.represent.return_value = []
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        assert engine.recognize_face(FAKE_IMAGE) == []

    def test_unknown_target_employee_id_returns_empty(self, engine, monkeypatch):
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']
        _patch_basic_decode(monkeypatch)
        fake_deepface = MagicMock()
        fake_deepface.represent.return_value = [{
            'embedding': [1.0, 0.0], 'facial_area': {'x': 0, 'y': 0, 'w': 100, 'h': 100}
        }]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = engine.recognize_face(FAKE_IMAGE, target_employee_id='999')
        assert result == []

    def test_skips_degenerate_bbox(self, engine, monkeypatch, dataset_and_model_dirs):
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']
        _patch_basic_decode(monkeypatch)
        fake_deepface = MagicMock()
        # Zero-area bbox -> degenerate detection, must be skipped entirely.
        fake_deepface.represent.return_value = [{
            'embedding': [1.0, 0.0], 'facial_area': {'x': 0, 'y': 0, 'w': 0, 'h': 0}
        }]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        assert engine.recognize_face(FAKE_IMAGE) == []

    def test_recognizes_close_match_within_tolerance(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        _make_employee_photo(dataset_dir, '1', 'a.jpg')
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']
        _patch_basic_decode(monkeypatch)

        fake_deepface = MagicMock()
        # Target embedding and cached employee embedding are identical unit
        # vectors -> cosine distance == 0.0, well within tolerance/margin.
        fake_deepface.represent.side_effect = [
            [{'embedding': [1.0, 0.0], 'facial_area': {'x': 0, 'y': 0, 'w': 100, 'h': 100}}],  # target frame
            [{'embedding': [1.0, 0.0]}],  # cached embedding computation for the stored photo
        ]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        results = engine.recognize_face(FAKE_IMAGE)

        assert len(results) == 1
        assert results[0]['name'] == 'Alice'
        assert results[0]['employee_id'] == '1'
        assert results[0]['confidence'] > 0.9

    def test_unknown_when_distance_exceeds_tolerance(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        _make_employee_photo(dataset_dir, '1', 'a.jpg')
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']
        _patch_basic_decode(monkeypatch)

        fake_deepface = MagicMock()
        # Orthogonal vectors -> cosine distance == 1.0, far beyond tolerance.
        fake_deepface.represent.side_effect = [
            [{'embedding': [1.0, 0.0], 'facial_area': {'x': 0, 'y': 0, 'w': 100, 'h': 100}}],
            [{'embedding': [0.0, 1.0]}],
        ]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        results = engine.recognize_face(FAKE_IMAGE)

        assert len(results) == 1
        assert results[0]['name'] == 'Unknown'
        assert results[0]['employee_id'] is None

    def test_unknown_when_ambiguous_margin_between_two_employees(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        _make_employee_photo(dataset_dir, '1', 'a.jpg')
        _make_employee_photo(dataset_dir, '2', 'a.jpg')
        engine.known_face_ids = ['1', '2']
        engine.known_face_names = ['Alice', 'Bob']
        _patch_basic_decode(monkeypatch)

        # Two candidate embeddings that both land close to the target, with
        # a gap smaller than MIN_MATCH_MARGIN between them -> ambiguous.
        # Angles chosen so both resulting cosine distances sit in
        # [EARLY_EXIT_DISTANCE, STRICT_MAX_TOLERANCE) = [0.18, 0.30): close
        # enough to both be viable matches, but NOT close enough to trigger
        # the early-exit "stop scanning other employees" shortcut (which
        # only fires below 0.18), so employee 2 is actually reached.
        import math
        target = [1.0, 0.0]

        def _emb_for(angle_deg):
            rad = math.radians(angle_deg)
            return [math.cos(rad), math.sin(rad)]

        fake_deepface = MagicMock()
        fake_deepface.represent.side_effect = [
            [{'embedding': target, 'facial_area': {'x': 0, 'y': 0, 'w': 100, 'h': 100}}],
            [{'embedding': _emb_for(40)}],   # employee 1's stored photo (distance ~0.234)
            [{'embedding': _emb_for(41)}],   # employee 2's stored photo (distance ~0.245) - margin ~0.011 < 0.05
        ]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        results = engine.recognize_face(FAKE_IMAGE)

        assert len(results) == 1
        assert results[0]['name'] == 'Unknown'

    def test_target_employee_id_filters_to_single_candidate(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        _make_employee_photo(dataset_dir, '1', 'a.jpg')
        _make_employee_photo(dataset_dir, '2', 'a.jpg')
        engine.known_face_ids = ['1', '2']
        engine.known_face_names = ['Alice', 'Bob']
        _patch_basic_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.represent.side_effect = [
            [{'embedding': [1.0, 0.0], 'facial_area': {'x': 0, 'y': 0, 'w': 100, 'h': 100}}],
            [{'embedding': [1.0, 0.0]}],  # only employee '1' should ever be checked
        ]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        results = engine.recognize_face(FAKE_IMAGE, target_employee_id='1')

        assert len(results) == 1
        assert results[0]['employee_id'] == '1'
        # represent() called exactly twice: once for the frame, once for
        # employee 1's single stored photo - employee 2 was never touched.
        assert fake_deepface.represent.call_count == 2

    def test_explicit_tolerance_argument_is_capped_at_strict_max(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        _make_employee_photo(dataset_dir, '1', 'a.jpg')
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']
        _patch_basic_decode(monkeypatch)

        # Distance of ~0.40 (angle 53 deg) would PASS a loose, uncapped
        # tolerance of 0.6, but must be rejected once the explicitly-passed
        # tolerance is hard-capped at STRICT_MAX_TOLERANCE (0.30) - this is
        # what actually distinguishes "capping happened" from "would have
        # failed anyway".
        import math
        rad = math.radians(53)  # cosine distance = 1 - cos(53deg) ~= 0.398
        fake_deepface = MagicMock()
        fake_deepface.represent.side_effect = [
            [{'embedding': [1.0, 0.0], 'facial_area': {'x': 0, 'y': 0, 'w': 100, 'h': 100}}],
            [{'embedding': [math.cos(rad), math.sin(rad)]}],
        ]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        results = engine.recognize_face(FAKE_IMAGE, tolerance=0.6)

        assert results[0]['name'] == 'Unknown'


# ---------------------------------------------------------------------
# rescan_and_train_untrained_employees
# ---------------------------------------------------------------------

class TestRescanAndTrainUntrainedEmployees:

    def test_returns_zero_when_deepface_or_cv2_missing(self, engine, monkeypatch):
        monkeypatch.setattr(ai_engine, 'DeepFace', None)
        assert engine.rescan_and_train_untrained_employees() == 0

    def test_returns_zero_when_dataset_folder_missing(self, engine, tmp_path, monkeypatch):
        monkeypatch.setattr(Config, 'DATASET_FOLDER', str(tmp_path / 'nope'))
        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)
        assert engine.rescan_and_train_untrained_employees() == 0

    def test_trains_untrained_employee_with_enough_images(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        monkeypatch.setattr(Config, 'MIN_FACE_IMAGES_REQUIRED', 1)
        _make_employee_photo(dataset_dir, '1', 'a.jpg')
        _patch_basic_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.extract_faces.return_value = [{'face': FAKE_IMAGE}]
        fake_deepface.represent.return_value = [{'embedding': [0.1]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        trained = engine.rescan_and_train_untrained_employees()

        assert trained == 1
        assert '1' in engine.known_face_ids

    def test_skips_already_known_employee(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        monkeypatch.setattr(Config, 'MIN_FACE_IMAGES_REQUIRED', 1)
        _make_employee_photo(dataset_dir, '1', 'a.jpg')
        engine.known_face_ids = ['1']
        engine.known_face_names = ['Alice']

        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        trained = engine.rescan_and_train_untrained_employees()

        assert trained == 0
        fake_deepface.extract_faces.assert_not_called()

    def test_skips_employee_with_too_few_images(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        monkeypatch.setattr(Config, 'MIN_FACE_IMAGES_REQUIRED', 5)
        _make_employee_photo(dataset_dir, '1', 'a.jpg')  # only 1 image, need 5

        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        trained = engine.rescan_and_train_untrained_employees()

        assert trained == 0
        fake_deepface.extract_faces.assert_not_called()

    def test_uses_db_employee_name_when_available(self, engine, monkeypatch, dataset_and_model_dirs,
                                                    app_context, make_employee):
        dataset_dir, _ = dataset_and_model_dirs
        monkeypatch.setattr(Config, 'MIN_FACE_IMAGES_REQUIRED', 1)
        employee = make_employee(employee_id='EMP0001', name='Real DB Name')
        _make_employee_photo(dataset_dir, str(employee.id), 'a.jpg')
        _patch_basic_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.extract_faces.return_value = [{'face': FAKE_IMAGE}]
        fake_deepface.represent.return_value = [{'embedding': [0.1]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        engine.rescan_and_train_untrained_employees()

        idx = engine.known_face_ids.index(str(employee.id))
        assert engine.known_face_names[idx] == 'Real DB Name'

    def test_falls_back_to_folder_name_without_db_context(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        monkeypatch.setattr(Config, 'MIN_FACE_IMAGES_REQUIRED', 1)
        _make_employee_photo(dataset_dir, '42', 'a.jpg')
        _patch_basic_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.extract_faces.return_value = [{'face': FAKE_IMAGE}]
        fake_deepface.represent.return_value = [{'embedding': [0.1]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        # No app_context fixture used here -> Employee.query lookup inside
        # rescan_and_train_untrained_employees fails and must be swallowed,
        # falling back to the folder name itself as the display name.
        engine.rescan_and_train_untrained_employees()

        idx = engine.known_face_ids.index('42')
        assert engine.known_face_names[idx] == '42'

    def test_continues_after_one_employee_training_failure(self, engine, monkeypatch, dataset_and_model_dirs):
        dataset_dir, _ = dataset_and_model_dirs
        monkeypatch.setattr(Config, 'MIN_FACE_IMAGES_REQUIRED', 1)
        _make_employee_photo(dataset_dir, '1', 'a.jpg')
        _make_employee_photo(dataset_dir, '2', 'a.jpg')
        _patch_basic_decode(monkeypatch)

        original_train_employee = engine.train_employee

        def _flaky_train(employee_id, employee_name, image_paths):
            if employee_id == '1':
                raise RuntimeError("disk error")
            return original_train_employee(employee_id, employee_name, image_paths)

        monkeypatch.setattr(engine, 'train_employee', _flaky_train)

        fake_deepface = MagicMock()
        fake_deepface.extract_faces.return_value = [{'face': FAKE_IMAGE}]
        fake_deepface.represent.return_value = [{'embedding': [0.1]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        trained = engine.rescan_and_train_untrained_employees()

        assert trained == 1
        assert engine.known_face_ids == ['2']


# ---------------------------------------------------------------------
# Throttled "no employees" warning / auto-reload cooldown
# ---------------------------------------------------------------------

class TestThrottlingHelpers:

    def test_warning_is_throttled_within_window(self, engine, monkeypatch):
        logged = []
        monkeypatch.setattr(ai_engine.logger, 'warning', lambda msg: logged.append(msg))

        engine._log_no_employees_warning_throttled()
        engine._log_no_employees_warning_throttled()

        assert len(logged) == 1

    def test_warning_logs_again_after_cooldown_elapses(self, engine, monkeypatch):
        logged = []
        monkeypatch.setattr(ai_engine.logger, 'warning', lambda msg: logged.append(msg))

        fake_time = [1000.0]
        monkeypatch.setattr(ai_engine.time, 'time', lambda: fake_time[0])

        engine._log_no_employees_warning_throttled()
        fake_time[0] += engine.NO_EMPLOYEES_WARNING_THROTTLE_SECONDS + 1
        engine._log_no_employees_warning_throttled()

        assert len(logged) == 2

    def test_auto_reload_skips_when_already_in_progress(self, engine, monkeypatch):
        engine._reload_in_progress = True
        calls = []
        monkeypatch.setattr(ai_engine.threading, 'Thread',
                             lambda *a, **k: calls.append(1) or MagicMock())

        engine._attempt_auto_reload()

        assert calls == []

    def test_auto_reload_skips_within_cooldown_window(self, engine, monkeypatch):
        fake_time = [1000.0]
        monkeypatch.setattr(ai_engine.time, 'time', lambda: fake_time[0])
        engine._last_reload_attempt_time = 995.0  # < AUTO_RELOAD_COOLDOWN_SECONDS ago

        calls = []
        monkeypatch.setattr(ai_engine.threading, 'Thread',
                             lambda *a, **k: calls.append(1) or MagicMock())

        engine._attempt_auto_reload()

        assert calls == []
