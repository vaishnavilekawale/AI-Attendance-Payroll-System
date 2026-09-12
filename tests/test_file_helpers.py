"""
Tests for file_helpers.py

Covers:
- allowed_file
"""
import pytest
from flask import Flask


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
    return app


def test_allowed_file_with_valid_extension(app):
    """Test that allowed_file returns True for valid extensions."""
    from file_helpers import allowed_file

    with app.app_context():
        assert allowed_file('photo.jpg') == True
        assert allowed_file('photo.jpeg') == True
        assert allowed_file('photo.png') == True
        assert allowed_file('photo.gif') == True
        assert allowed_file('document.pdf') == True


def test_allowed_file_with_invalid_extension(app):
    """Test that allowed_file returns False for invalid extensions."""
    from file_helpers import allowed_file

    with app.app_context():
        assert allowed_file('photo.bmp') == False
        assert allowed_file('document.doc') == False
        assert allowed_file('document.docx') == False
        assert allowed_file('archive.zip') == False


def test_allowed_file_with_no_extension(app):
    """Test that allowed_file returns False for files without extension."""
    from file_helpers import allowed_file

    with app.app_context():
        assert allowed_file('photo') == False


def test_allowed_file_with_multiple_dots(app):
    """Test that allowed_file handles filenames with multiple dots."""
    from file_helpers import allowed_file

    with app.app_context():
        assert allowed_file('photo.final.jpg') == True
        assert allowed_file('photo.final.bmp') == False


def test_allowed_file_case_insensitive(app):
    """Test that allowed_file is case-insensitive."""
    from file_helpers import allowed_file

    with app.app_context():
        assert allowed_file('photo.JPG') == True
        assert allowed_file('photo.Jpg') == True
        assert allowed_file('photo.PNG') == True
        assert allowed_file('photo.Pdf') == True


def test_allowed_file_empty_filename(app):
    """Test that allowed_file handles empty filename."""
    from file_helpers import allowed_file

    with app.app_context():
        assert allowed_file('') == False


def test_allowed_file_only_extension(app):
    """Test that allowed_file handles filenames with only extension."""
    from file_helpers import allowed_file

    with app.app_context():
        assert allowed_file('.jpg') == True
        assert allowed_file('.bmp') == False
