"""
Regression tests for "EMP0001 stands in front of the camera, EMP0002 gets marked".

Three root causes in FaceRecognitionEngine.recognize_face, each covered here:
  1. EARLY EXIT: scanning stopped at the first employee (in list order) whose
     photo was under EARLY_EXIT_DISTANCE, so a second, CLOSER employee was never
     compared and the ambiguity margin was never applied.
  2. TARGETED (employee-login) CHECK compared the face only with the logged-in
     employee's photos, so any face within the tolerance of THEIR photos
     passed - even a face that is clearly someone else's.
  3. ONE head, TWO detections (overlapping boxes) -> two different employees
     could be marked from one person.
"""
import math
from unittest.mock import MagicMock

import numpy as np
import pytest

import ai_engine
from ai_engine import FaceRecognitionEngine
from config import Config

FAKE_IMAGE = np.zeros((20, 20, 3), dtype=np.uint8)
BOX = {'x': 0, 'y': 0, 'w': 100, 'h': 100}


@pytest.fixture(autouse=True)
def _clean_cache():
    ai_engine.clear_embeddings_cache()
    yield
    ai_engine.clear_embeddings_cache()


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    dataset, model = tmp_path / "dataset", tmp_path / "trained_model"
    dataset.mkdir()
    model.mkdir()
    monkeypatch.setattr(Config, 'DATASET_FOLDER', str(dataset))
    monkeypatch.setattr(Config, 'TRAINED_MODEL_FOLDER', str(model))
    monkeypatch.setattr(ai_engine, 'EMBEDDINGS_CACHE_FILE', str(model / 'embeddings_cache.pkl'))
    return dataset


def _emb(angle_deg):
    """Unit vector at an angle; cosine distance to [1, 0] is 1 - cos(angle)."""
    r = math.radians(angle_deg)
    return [math.cos(r), math.sin(r)]


def _setup(dirs, monkeypatch, employees, photo_angles, faces):
    """
    employees: list of ids in enrolment order; photo_angles: one angle per employee;
    faces: list of (embedding, bbox) the camera frame contains.
    """
    for emp in employees:
        d = dirs / emp
        d.mkdir()
        (d / 'a.jpg').write_bytes(b'x')
    engine = FaceRecognitionEngine()
    engine.known_face_ids = list(employees)
    engine.known_face_names = [f"Emp{e}" for e in employees]

    monkeypatch.setattr(ai_engine.crypto_utils, 'is_encrypted', lambda raw: False)
    monkeypatch.setattr(ai_engine.cv2, 'imdecode', lambda arr, flag: FAKE_IMAGE)
    monkeypatch.setattr(ai_engine.cv2, 'resize', lambda frame, size: frame)

    fake = MagicMock()
    fake.represent.side_effect = (
        [[{'embedding': e, 'facial_area': b} for e, b in faces]]
        + [[{'embedding': _emb(a)}] for a in photo_angles]
    )
    monkeypatch.setattr(ai_engine, 'DeepFace', fake)
    return engine


def test_closer_employee_wins_even_if_listed_later(dirs, monkeypatch):
    # Face is almost exactly employee 2 (10 deg, distance ~0.015). Employee 1 is
    # listed first and is "fairly close" (30 deg, distance ~0.134 < 0.18).
    engine = _setup(dirs, monkeypatch, ['1', '2'], [30, 10], [([1.0, 0.0], BOX)])
    (result,) = engine.recognize_face(FAKE_IMAGE)
    assert result['employee_id'] == '2'


def test_lookalikes_are_rejected_even_under_the_early_exit_distance(dirs, monkeypatch):
    # Two employees both ~0.06-0.07 away, only ~0.012 apart: ambiguous -> nobody is marked.
    engine = _setup(dirs, monkeypatch, ['1', '2'], [20, 22], [([1.0, 0.0], BOX)])
    (result,) = engine.recognize_face(FAKE_IMAGE)
    assert result['employee_id'] is None and result['name'] == 'Unknown'


def test_employee_login_rejects_someone_elses_face(dirs, monkeypatch):
    # Employee 2 is logged in, but the face is exactly employee 1's. Employee 2's
    # own photo is 25 deg away (distance ~0.094): inside the tolerance on its own.
    engine = _setup(dirs, monkeypatch, ['1', '2'], [0, 25], [([1.0, 0.0], BOX)])
    (result,) = engine.recognize_face(FAKE_IMAGE, target_employee_id='2')
    assert result['employee_id'] is None


def test_employee_login_accepts_the_right_face(dirs, monkeypatch):
    engine = _setup(dirs, monkeypatch, ['1', '2'], [0, 70], [([1.0, 0.0], BOX)])
    (result,) = engine.recognize_face(FAKE_IMAGE, target_employee_id='1')
    assert result['employee_id'] == '1'


def test_employee_login_with_a_single_enrolled_employee_still_works(dirs, monkeypatch):
    engine = _setup(dirs, monkeypatch, ['1'], [5], [([1.0, 0.0], BOX)])
    (result,) = engine.recognize_face(FAKE_IMAGE, target_employee_id='1')
    assert result['employee_id'] == '1'


def test_one_head_detected_twice_does_not_mark_two_employees(dirs, monkeypatch):
    big = {'x': 100, 'y': 100, 'w': 200, 'h': 200}
    inner = {'x': 140, 'y': 140, 'w': 80, 'h': 80}   # false-positive box inside the same head
    engine = _setup(dirs, monkeypatch, ['1', '2'], [0, 80],
                    [([1.0, 0.0], big), (_emb(80), inner)])
    results = engine.recognize_face(FAKE_IMAGE)
    assert [r['employee_id'] for r in results] == ['1']


def test_same_employee_matched_by_two_separate_faces_is_marked_once(dirs, monkeypatch):
    left = {'x': 0, 'y': 0, 'w': 100, 'h': 100}
    right = {'x': 300, 'y': 0, 'w': 100, 'h': 100}
    engine = _setup(dirs, monkeypatch, ['1'], [0],
                    [([1.0, 0.0], left), (_emb(3), right)])
    results = engine.recognize_face(FAKE_IMAGE)
    ids = [r['employee_id'] for r in results]
    assert ids.count('1') == 1 and ids.count(None) == 1


def test_two_genuinely_different_people_are_both_recognised(dirs, monkeypatch):
    left = {'x': 0, 'y': 0, 'w': 100, 'h': 100}
    right = {'x': 300, 'y': 0, 'w': 100, 'h': 100}
    engine = _setup(dirs, monkeypatch, ['1', '2'], [0, 90],
                    [([1.0, 0.0], left), (_emb(90), right)])
    results = engine.recognize_face(FAKE_IMAGE)
    assert sorted(r['employee_id'] for r in results) == ['1', '2']
