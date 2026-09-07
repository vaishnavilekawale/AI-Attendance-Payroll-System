"""
The licensing/ test suite is completely independent of the Flask app (it
tests license_manager.py in isolation, with real generated keys/licenses)
- it never touches extensions.limiter or email_service.EmailService at
all. Without this override, pytest still applies the ROOT conftest.py's
autouse fixtures (_reset_rate_limits, _no_real_email_sending) to every
test it collects anywhere under the project root, including these -
and _reset_rate_limits specifically fails here because it assumes the
main Flask app/limiter has already been initialized via the `flask_app`/
`app_context` fixtures, which these tests never request.

Redefining the same fixture names here, as no-ops, is the standard pytest
pattern for "a subdirectory's tests need different fixture behavior than
the parent conftest.py provides" - the nearest conftest.py wins.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    yield


@pytest.fixture(autouse=True)
def _no_real_email_sending():
    yield
