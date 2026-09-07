"""
File-upload helper(s), extracted out of app.py so blueprints can use them
without a circular import.

Uses Flask's `current_app` proxy rather than importing the `app` object
directly - the correct, idiomatic way to reach app.config from code that
needs to work both inside app.py itself and inside any blueprint.
"""
from flask import current_app


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']
