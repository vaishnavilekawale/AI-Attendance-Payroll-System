"""
Tests for the "dataset mapping" layer of ai_engine.py: the code that maps
on-disk dataset images (Config.DATASET_FOLDER/<employee_id>/<file>.jpg) to
cached Facenet512 embeddings, and the two-level (in-memory + on-disk
pickle) cache around it.

Covers: load_face_image_array, get_employee_embedding_cached,
preload_employee_embeddings, clear_embeddings_cache,
_load_persistent_embeddings_cache / _save_persistent_embeddings_cache,
and get_recognition_tolerance.

cv2/mediapipe/tensorflow/deepface are already stubbed globally in
conftest.py (so importing ai_engine never pulls in the real, multi-
gigabyte ML stack). These tests monkeypatch the specific stubbed
attributes (ai_engine.cv2.imdecode, ai_engine.DeepFace) per-test to
control exactly what "detection"/"embedding" behaviour is exercised,
without needing the real libraries installed.
"""
import os
import pickle
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

import ai_engine
from config import Config


FAKE_IMAGE = np.zeros((20, 20, 3), dtype=np.uint8)


@pytest.fixture(autouse=True)
def _isolate_embeddings_cache():
    """Every test starts and ends with a clean in-memory embeddings cache,
    since it's process-global module state shared across tests."""
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
    cache_file = str(model_dir / 'embeddings_cache.pkl')
    monkeypatch.setattr(ai_engine, 'EMBEDDINGS_CACHE_FILE', cache_file)
    return dataset_dir, model_dir


def _write_plain_image(path):
    # load_face_image_array only cares that the bytes aren't a valid
    # crypto_utils-encrypted token; any bytes work for a "plain" image
    # once cv2.imdecode is mocked to ignore its input and return a fixed
    # array.
    path.write_bytes(b'\xff\xd8\xff\xfake-jpeg-bytes')


# ---------------------------------------------------------------------
# load_face_image_array
# ---------------------------------------------------------------------

class TestLoadFaceImageArray:

    def test_loads_plain_unencrypted_image(self, tmp_path, monkeypatch):
        img_path = tmp_path / "photo.jpg"
        _write_plain_image(img_path)

        monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: FAKE_IMAGE)
        monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)

        result = ai_engine.load_face_image_array(str(img_path))
        assert result is FAKE_IMAGE

    def test_decrypts_encrypted_image_before_decoding(self, tmp_path, monkeypatch):
        img_path = tmp_path / "photo.jpg"
        img_path.write_bytes(b'encrypted-token-bytes')

        decrypt_calls = []

        def _fake_decrypt(raw):
            decrypt_calls.append(raw)
            return b'decrypted-plain-bytes'

        monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: True)
        monkeypatch.setattr(ai_engine.crypto_utils, 'decrypt_bytes', _fake_decrypt)
        monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: FAKE_IMAGE)

        result = ai_engine.load_face_image_array(str(img_path))

        assert result is FAKE_IMAGE
        assert decrypt_calls == [b'encrypted-token-bytes']

    def test_returns_none_when_decode_fails(self, tmp_path, monkeypatch):
        img_path = tmp_path / "corrupt.jpg"
        _write_plain_image(img_path)

        monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)
        monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: None)

        assert ai_engine.load_face_image_array(str(img_path)) is None

    def test_raises_for_missing_file(self, tmp_path):
        missing = tmp_path / "nope.jpg"
        with pytest.raises(FileNotFoundError):
            ai_engine.load_face_image_array(str(missing))


# ---------------------------------------------------------------------
# get_employee_embedding_cached
# ---------------------------------------------------------------------

class TestGetEmployeeEmbeddingCached:

    def _patch_decode(self, monkeypatch):
        monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)
        monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: FAKE_IMAGE)

    def test_computes_and_caches_new_embedding(self, tmp_path, monkeypatch):
        img_path = tmp_path / "1.jpg"
        _write_plain_image(img_path)
        self._patch_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.represent.return_value = [{'embedding': [0.1, 0.2, 0.3]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        embedding = ai_engine.get_employee_embedding_cached('1', str(img_path))

        assert embedding == [0.1, 0.2, 0.3]
        fake_deepface.represent.assert_called_once()
        cache_key = f"1_{os.path.basename(str(img_path))}"
        assert cache_key in ai_engine._employee_embeddings_cache

    def test_returns_cached_value_when_mtime_unchanged(self, tmp_path, monkeypatch):
        img_path = tmp_path / "1.jpg"
        _write_plain_image(img_path)
        self._patch_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.represent.return_value = [{'embedding': [1.0, 2.0]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        first = ai_engine.get_employee_embedding_cached('1', str(img_path))
        second = ai_engine.get_employee_embedding_cached('1', str(img_path))

        assert first == second == [1.0, 2.0]
        # Second call must be served from cache - DeepFace only invoked once.
        fake_deepface.represent.assert_called_once()

    def test_recomputes_when_file_mtime_changes(self, tmp_path, monkeypatch):
        img_path = tmp_path / "1.jpg"
        _write_plain_image(img_path)
        self._patch_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.represent.side_effect = [
            [{'embedding': [1.0]}],
            [{'embedding': [2.0]}],
        ]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        first = ai_engine.get_employee_embedding_cached('1', str(img_path))
        # Simulate the photo being replaced (new mtime).
        future = time.time() + 5
        os.utime(str(img_path), (future, future))
        second = ai_engine.get_employee_embedding_cached('1', str(img_path))

        assert first == [1.0]
        assert second == [2.0]
        assert fake_deepface.represent.call_count == 2

    def test_returns_none_when_image_cannot_be_decoded(self, tmp_path, monkeypatch):
        img_path = tmp_path / "1.jpg"
        _write_plain_image(img_path)
        monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)
        monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: None)

        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = ai_engine.get_employee_embedding_cached('1', str(img_path))

        assert result is None
        fake_deepface.represent.assert_not_called()

    def test_returns_none_when_deepface_raises(self, tmp_path, monkeypatch):
        img_path = tmp_path / "1.jpg"
        _write_plain_image(img_path)
        self._patch_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.represent.side_effect = RuntimeError("model blew up")
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        assert ai_engine.get_employee_embedding_cached('1', str(img_path)) is None

    def test_returns_none_when_represent_returns_empty_list(self, tmp_path, monkeypatch):
        img_path = tmp_path / "1.jpg"
        _write_plain_image(img_path)
        self._patch_decode(monkeypatch)

        fake_deepface = MagicMock()
        fake_deepface.represent.return_value = []
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        assert ai_engine.get_employee_embedding_cached('1', str(img_path)) is None

    def test_handles_missing_file_mtime_gracefully(self, monkeypatch):
        # File doesn't exist -> os.path.getmtime raises OSError, current_mtime
        # becomes None, but load_face_image_array then raises FileNotFoundError
        # which must be caught (not propagated) and reported as None.
        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = ai_engine.get_employee_embedding_cached('1', '/no/such/path/1.jpg')
        assert result is None


# ---------------------------------------------------------------------
# clear_embeddings_cache / persistence round-trip
# ---------------------------------------------------------------------

class TestEmbeddingsCachePersistence:

    def test_clear_embeddings_cache_empties_in_memory_dict(self):
        ai_engine._employee_embeddings_cache['x_1.jpg'] = {
            'embedding': [1], 'mtime': 1.0, 'employee_id': 'x'
        }
        ai_engine.clear_embeddings_cache()
        assert ai_engine._employee_embeddings_cache == {}

    def test_save_then_load_round_trip(self, dataset_and_model_dirs):
        ai_engine._employee_embeddings_cache['1_a.jpg'] = {
            'embedding': [1.0, 2.0], 'mtime': 123.0, 'employee_id': '1'
        }
        ai_engine._save_persistent_embeddings_cache()

        assert os.path.exists(ai_engine.EMBEDDINGS_CACHE_FILE)

        ai_engine.clear_embeddings_cache()
        assert ai_engine._employee_embeddings_cache == {}

        ai_engine._load_persistent_embeddings_cache()
        assert ai_engine._employee_embeddings_cache['1_a.jpg']['embedding'] == [1.0, 2.0]

    def test_load_is_noop_when_no_cache_file_exists(self, dataset_and_model_dirs):
        # No exception, no-op, cache stays empty.
        ai_engine._load_persistent_embeddings_cache()
        assert ai_engine._employee_embeddings_cache == {}

    def test_load_handles_corrupted_cache_file_gracefully(self, dataset_and_model_dirs):
        with open(ai_engine.EMBEDDINGS_CACHE_FILE, 'wb') as f:
            f.write(b'not a valid pickle')

        # Must not raise - falls back to starting fresh.
        ai_engine._load_persistent_embeddings_cache()
        assert ai_engine._employee_embeddings_cache == {}

    def test_save_creates_trained_model_folder_if_missing(self, tmp_path, monkeypatch):
        model_dir = tmp_path / "brand_new_trained_model"
        monkeypatch.setattr(Config, 'TRAINED_MODEL_FOLDER', str(model_dir))
        monkeypatch.setattr(ai_engine, 'EMBEDDINGS_CACHE_FILE', str(model_dir / 'embeddings_cache.pkl'))
        assert not model_dir.exists()

        ai_engine._save_persistent_embeddings_cache()

        assert model_dir.exists()
        assert os.path.exists(ai_engine.EMBEDDINGS_CACHE_FILE)

    def test_save_swallows_write_errors(self, monkeypatch, tmp_path):
        # Point at a path whose parent directory can never be created
        # (a file sitting where a directory needs to go) to force an
        # exception inside _save_persistent_embeddings_cache - it must be
        # caught and logged, not raised.
        blocker_file = tmp_path / "not_a_directory"
        blocker_file.write_text("x")
        bogus_model_dir = blocker_file / "trained_model"

        monkeypatch.setattr(Config, 'TRAINED_MODEL_FOLDER', str(bogus_model_dir))
        monkeypatch.setattr(ai_engine, 'EMBEDDINGS_CACHE_FILE', str(bogus_model_dir / 'embeddings_cache.pkl'))

        # Should not raise.
        ai_engine._save_persistent_embeddings_cache()


# ---------------------------------------------------------------------
# preload_employee_embeddings
# ---------------------------------------------------------------------

class TestPreloadEmployeeEmbeddings:

    def test_returns_zeroed_stats_when_deepface_not_installed(self, monkeypatch):
        monkeypatch.setattr(ai_engine, 'DeepFace', None)
        result = ai_engine.preload_employee_embeddings()
        assert result == {'total': 0, 'already_cached': 0, 'encoded': 0, 'errors': 0, 'seconds': 0.0}

    def test_returns_zeroed_stats_when_dataset_folder_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "does_not_exist"
        monkeypatch.setattr(Config, 'DATASET_FOLDER', str(missing_dir))
        monkeypatch.setattr(Config, 'TRAINED_MODEL_FOLDER', str(tmp_path / 'trained_model'))
        monkeypatch.setattr(ai_engine, 'EMBEDDINGS_CACHE_FILE', str(tmp_path / 'trained_model' / 'cache.pkl'))
        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = ai_engine.preload_employee_embeddings()

        assert result['total'] == 0
        fake_deepface.represent.assert_not_called()

    def test_encodes_new_images_and_reports_counts(self, dataset_and_model_dirs, monkeypatch):
        dataset_dir, model_dir = dataset_and_model_dirs
        emp1_dir = dataset_dir / "1"
        emp1_dir.mkdir()
        (emp1_dir / "a.jpg").write_bytes(b'imgbytesA')
        (emp1_dir / "b.jpg").write_bytes(b'imgbytesB')
        # Non-image file must be ignored entirely.
        (emp1_dir / "notes.txt").write_text("ignore me")
        # A stray file directly under DATASET_FOLDER (not a directory) is skipped.
        (dataset_dir / "stray.jpg").write_bytes(b'stray')

        monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)
        monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: FAKE_IMAGE)

        fake_deepface = MagicMock()
        fake_deepface.represent.return_value = [{'embedding': [0.5]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = ai_engine.preload_employee_embeddings(max_workers=2)

        assert result['total'] == 2
        assert result['encoded'] == 2
        assert result['errors'] == 0
        assert result['already_cached'] == 0
        assert os.path.exists(ai_engine.EMBEDDINGS_CACHE_FILE)

    def test_second_run_reports_images_as_already_cached(self, dataset_and_model_dirs, monkeypatch):
        dataset_dir, model_dir = dataset_and_model_dirs
        emp1_dir = dataset_dir / "1"
        emp1_dir.mkdir()
        (emp1_dir / "a.jpg").write_bytes(b'imgbytesA')

        monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)
        monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: FAKE_IMAGE)
        fake_deepface = MagicMock()
        fake_deepface.represent.return_value = [{'embedding': [0.5]}]
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        first = ai_engine.preload_employee_embeddings(max_workers=2)
        assert first['encoded'] == 1
        assert first['already_cached'] == 0

        second = ai_engine.preload_employee_embeddings(max_workers=2)
        assert second['already_cached'] == 1
        # Note: encoded counts successful retrievals (cached or new), not just new encodings
        assert second['encoded'] == 1

    def test_counts_errors_when_embedding_fails(self, dataset_and_model_dirs, monkeypatch):
        dataset_dir, model_dir = dataset_and_model_dirs
        emp1_dir = dataset_dir / "1"
        emp1_dir.mkdir()
        (emp1_dir / "a.jpg").write_bytes(b'imgbytesA')

        monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)
        monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: None)  # decode fails
        fake_deepface = MagicMock()
        monkeypatch.setattr(ai_engine, 'DeepFace', fake_deepface)

        result = ai_engine.preload_employee_embeddings(max_workers=2)

        assert result['total'] == 1
        assert result['errors'] == 1
        assert result['encoded'] == 0


# ---------------------------------------------------------------------
# get_recognition_tolerance
# ---------------------------------------------------------------------

class TestGetRecognitionTolerance:

    def test_defaults_to_0_3_cap_without_app_context(self):
        # No Settings/DB context available at all -> falls back to the
        # generic 0.6 default, then hard-capped at STRICT_MAX_TOLERANCE.
        assert ai_engine.get_recognition_tolerance() == ai_engine.STRICT_MAX_TOLERANCE

    def test_uses_configured_value_when_below_cap(self, app_context):
        from models import Settings
        settings = Settings.get_settings()
        settings.face_recognition_tolerance = 0.2
        from database import db
        db.session.commit()

        assert ai_engine.get_recognition_tolerance() == 0.2

    def test_caps_configured_value_at_strict_max(self, app_context):
        from models import Settings
        settings = Settings.get_settings()
        settings.face_recognition_tolerance = 0.9  # looser than the ceiling
        from database import db
        db.session.commit()

        assert ai_engine.get_recognition_tolerance() == ai_engine.STRICT_MAX_TOLERANCE

    def test_falls_back_on_unexpected_error(self, app_context, monkeypatch):
        from models import Settings
        monkeypatch.setattr(Settings, 'get_settings',
                             staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("db error"))))

        assert ai_engine.get_recognition_tolerance() == ai_engine.STRICT_MAX_TOLERANCE
