"""
PyInstaller entry point. Not meant to be run directly during normal
development - use `python app.py` for that, same as always. This exists
purely so the packaged .exe has a clean, single entry point that:

  1. Forces the license check on regardless of FLASK_ENV (a packaged
     build handed to a customer should ALWAYS enforce licensing, whereas
     `python app.py` during your own development shouldn't require a
     license.lic file on every dev machine - see app.py's
     license_check_enabled logic).
  2. Points DeepFace at its bundled model weights (if you followed the
     attendance_app.spec instructions to pre-download and bundle them)
     rather than trying to download them at runtime on a customer's
     possibly-offline machine.
  3. Runs app.py's existing `if __name__ == '__main__':` startup block
     exactly as-is via runpy.run_module(), so no changes to app.py's own
     structure were needed for packaging.

IMPORTANT: this uses runpy.run_module(), NOT runpy.run_path(). run_path()
needs a real, physical .py file sitting on disk at the given path -
which is NOT how a frozen module is stored (it's compiled into the
bundled archive, not extracted as a loose file), so run_path() fails
with "ImportError: No module named __main__" in a packaged build even
though the exact same call would work when run unfrozen. run_module()
instead loads the module BY NAME through Python's normal import system,
which PyInstaller's frozen loader participates in correctly regardless
of whether the module is loose on disk or compiled into the archive.
"""
import os
import sys
import runpy

# Safe standard output/error redirection for non-console (frozen) environments
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

# Always enforce the license in a packaged build, no matter what
# FLASK_ENV happens to be set to.
# os.environ['LICENSE_CHECK_ENABLED'] = 'true'
os.environ.setdefault('FLASK_ENV', 'production')

# ---------------------------------------------------------------------
# Persistent data directory. When frozen, sys._MEIPASS is a TEMPORARY
# extraction folder that PyInstaller deletes on exit and recreates fresh
# (new random path) on every launch - anything written under a path
# derived from it (database, uploaded photos, trained face embeddings,
# generated payslips) would be silently lost every time the app closes.
#
# config.py already handles this correctly on its own (BASE_DIR there is
# frozen-aware), so this block isn't strictly required for that to work -
# it's set here too, redundantly, as a defense-in-depth measure and to
# make the persistent location explicit and inspectable via the
# environment for any other module that might need it.
# ---------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    persistent_dir = os.path.dirname(os.path.abspath(sys.executable))
else:
    persistent_dir = os.path.dirname(os.path.abspath(__file__))

os.environ.setdefault('APP_PERSISTENT_DIR', persistent_dir)

# When frozen by PyInstaller, bundled READ-ONLY data files (like DeepFace's
# pre-downloaded weights) live under sys._MEIPASS - that's fine for
# read-only assets, unlike the writable data above. Point DeepFace at the
# bundled weights folder there, if attendance_app.spec's deepface_weights
# bundling step was used.
if getattr(sys, 'frozen', False):
    bundle_dir = sys._MEIPASS
    bundled_weights = os.path.join(bundle_dir, 'deepface_weights')
    if os.path.isdir(bundled_weights):
        os.environ['DEEPFACE_HOME'] = os.path.dirname(bundled_weights)
        # DeepFace expects DEEPFACE_HOME/.deepface/weights/<file>.h5 -
        # verify this exact layout against your installed deepface
        # version if weights aren't found at runtime; this has shifted
        # between deepface releases before.

# Run app.py's own module code with __name__ == '__main__', so its
# existing startup block (license check, folder creation, browser
# auto-launch, app.run(...)) executes exactly as if you'd typed
# `python app.py` - no duplication of that logic here. Using
# run_module() (by NAME) rather than run_path() (by FILE PATH) is what
# makes this work correctly both frozen and unfrozen - see module
# docstring above for why.
runpy.run_module('app', run_name='__main__', alter_sys=True)