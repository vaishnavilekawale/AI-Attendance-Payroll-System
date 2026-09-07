"""
Shared Flask extension instances, uninitialized.

This module exists so that app.py AND every blueprint can import the same
`csrf` / `limiter` objects without circular imports (app.py importing a
blueprint, which imports app.py, which imports the blueprint...).

Usage in app.py (application factory pattern):

    from extensions import csrf, limiter
    csrf.init_app(app)
    limiter.init_app(app)

Usage in any blueprint:

    from extensions import limiter

    @setup_bp.route('/setup', methods=['GET', 'POST'])
    @limiter.limit("5 per minute")
    def step1_admin():
        ...

`db` already lives in its own neutral module (database.py) for the same
reason - this file follows the same pattern for the other two extensions
added during the security hardening pass.
"""
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

csrf = CSRFProtect()
limiter = Limiter(get_remote_address, default_limits=[])
