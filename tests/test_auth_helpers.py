"""
Tests for auth_helpers.py

Covers:
- create_admin_from_request_form
"""
import pytest
from flask import Flask
from unittest.mock import Mock, patch


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-secret'
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_create_admin_from_request_form_success(app_context, make_admin):
    """Test successful admin creation from request form."""
    from auth_helpers import create_admin_from_request_form
    from models import Admin
    from database import db
    from flask import request

    # Mock request.form
    with app_context.test_request_context(method='POST', data={
        'username': 'testadmin',
        'email': 'test@example.com',
        'password': 'password123',
        'confirm_password': 'password123'
    }):
        admin = create_admin_from_request_form()

        assert admin is not None
        assert admin.username == 'testadmin'
        assert admin.email == 'test@example.com'
        assert admin.check_password('password123')


def test_create_admin_from_request_form_missing_fields(app_context):
    """Test admin creation fails when required fields are missing."""
    from auth_helpers import create_admin_from_request_form
    from flask import flash

    with app_context.test_request_context(method='POST', data={
        'username': 'testadmin',
        'email': '',
        'password': 'password123',
        'confirm_password': 'password123'
    }):
        admin = create_admin_from_request_form()
        assert admin is None


def test_create_admin_from_request_form_username_too_short(app_context):
    """Test admin creation fails when username is too short."""
    from auth_helpers import create_admin_from_request_form

    with app_context.test_request_context(method='POST', data={
        'username': 'ab',
        'email': 'test@example.com',
        'password': 'password123',
        'confirm_password': 'password123'
    }):
        admin = create_admin_from_request_form()
        assert admin is None


def test_create_admin_from_request_form_password_too_short(app_context):
    """Test admin creation fails when password is too short."""
    from auth_helpers import create_admin_from_request_form

    with app_context.test_request_context(method='POST', data={
        'username': 'testadmin',
        'email': 'test@example.com',
        'password': '12345',
        'confirm_password': '12345'
    }):
        admin = create_admin_from_request_form()
        assert admin is None


def test_create_admin_from_request_form_password_mismatch(app_context):
    """Test admin creation fails when passwords don't match."""
    from auth_helpers import create_admin_from_request_form

    with app_context.test_request_context(method='POST', data={
        'username': 'testadmin',
        'email': 'test@example.com',
        'password': 'password123',
        'confirm_password': 'password456'
    }):
        admin = create_admin_from_request_form()
        assert admin is None


def test_create_admin_from_request_form_duplicate_username(app_context, make_admin):
    """Test admin creation fails when username already exists."""
    from auth_helpers import create_admin_from_request_form
    from database import db

    # Create existing admin
    existing_admin = make_admin(username='testadmin', email='existing@example.com')

    with app_context.test_request_context(method='POST', data={
        'username': 'testadmin',
        'email': 'new@example.com',
        'password': 'password123',
        'confirm_password': 'password123'
    }):
        admin = create_admin_from_request_form()
        assert admin is None


def test_create_admin_from_request_form_duplicate_email(app_context, make_admin):
    """Test admin creation fails when email already exists."""
    from auth_helpers import create_admin_from_request_form

    # Create existing admin
    existing_admin = make_admin(username='existing', email='test@example.com')

    with app_context.test_request_context(method='POST', data={
        'username': 'newadmin',
        'email': 'test@example.com',
        'password': 'password123',
        'confirm_password': 'password123'
    }):
        admin = create_admin_from_request_form()
        assert admin is None
