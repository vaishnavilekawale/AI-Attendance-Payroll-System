# attendance_app.spec
#
# PyInstaller build spec for the AI Attendance & Payroll System.
#
# IMPORTANT HONESTY NOTE: this spec is a carefully-constructed starting
# point based on well-documented PyInstaller behavior with this exact
# dependency stack - it has NOT been run end-to-end against a real build
# (that stack is several GB and takes a long time to install/build).
# Verifying it end-to-end on a real Windows machine is the next step.
# Treat the checklist at the bottom of this file as mandatory, not optional.
#
# Build with:
#   pip install pyinstaller pyinstaller-hooks-contrib
#   pyinstaller attendance_app.spec
#
# pyinstaller-hooks-contrib specifically matters here: it ships
# community-maintained hooks for tensorflow/mediapipe/PIL's hidden imports
# and data files, which vanilla PyInstaller does NOT reliably discover on
# its own - skipping it is the single most common cause of
# "ModuleNotFoundError" or "ImportError: DLL load failed" surprises at
# runtime in the built exe even though the app runs fine with `python app.py`.

import os
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    collect_dynamic_libs,
    copy_metadata,
)

block_cipher = None
PROJECT_ROOT = os.path.abspath('.')

# ---------------------------------------------------------------------
# Data files: everything the app reads off disk by relative path at
# runtime, which PyInstaller has no way to know about automatically since
# it only traces Python imports, not open()/render_template() calls -
# plus package-bundled data files that several dependencies ship
# alongside their code (fonts, cascade XML, compiled model files, etc).
#
# NOTE: do NOT manually loop over the project's own .py files and add
# them as `datas` here. PyInstaller's Analysis step (below) already
# traces every import reachable from launcher.py and compiles those
# modules into the frozen bundle correctly - copying loose .py source
# files on top of that (a) is redundant, (b) risks two independently-
# imported copies of the same module existing at runtime depending on
# sys.path order (a real source of subtle bugs for shared singletons
# like extensions.py's csrf/limiter/db objects), and (c) ships plain,
# human-readable source next to the exe that a customer never needs.
# ---------------------------------------------------------------------
datas = [
    (os.path.join(PROJECT_ROOT, 'templates'), 'templates'),
    (os.path.join(PROJECT_ROOT, 'static'), 'static'),
    (os.path.join(PROJECT_ROOT, 'dataset'), 'dataset'),
    (os.path.join(PROJECT_ROOT, 'uploads'), 'uploads'),
    (os.path.join(PROJECT_ROOT, 'uploads', 'payrolls'), os.path.join('uploads', 'payrolls')),
    (os.path.join(PROJECT_ROOT, 'trained_model'), 'trained_model'),
]

# DeepFace downloads its model weight files (e.g. facenet512_weights.h5)
# to ~/.deepface/weights the FIRST time it runs, if they aren't already
# there. A customer's machine will very likely have no internet access
# at the moment they first launch the packaged exe (or may be fully
# offline), so the weights MUST be pre-downloaded on the BUILD machine
# and bundled in - do not skip this step:
#   1. On your build machine: run the app normally once with
#      `python app.py` and actually trigger a face capture/training
#      action, so DeepFace downloads its weights to ~/.deepface/weights.
#   2. Build with the weights folder auto-detected (default) or point
#      DEEPFACE_WEIGHTS_DIR at a custom location, e.g.:
#        set DEEPFACE_WEIGHTS_DIR=C:\path\to\.deepface\weights   (Windows)
#        pyinstaller attendance_app.spec --clean
#   3. See the runtime note in launcher.py about DEEPFACE_HOME, which
#      points the frozen app at this bundled folder inside sys._MEIPASS.
#
# This is auto-detected rather than requiring you to hand-edit this spec
# file every build: if the weights aren't found, the build still
# succeeds (so a dev machine without a GPU/webcam can still smoke-test
# packaging) but prints a loud warning, since shipping without them means
# the customer's exe will try to hit the internet on first face capture.
deepface_weights_dir = os.environ.get(
    'DEEPFACE_WEIGHTS_DIR',
    os.path.expanduser(os.path.join('~', '.deepface', 'weights'))
)
if os.path.isdir(deepface_weights_dir) and os.listdir(deepface_weights_dir):
    datas.append((deepface_weights_dir, 'deepface_weights'))
    print(f"[spec] Bundling DeepFace weights from: {deepface_weights_dir}")
else:
    print(
        f"[spec] WARNING: DeepFace weights folder not found or empty at "
        f"'{deepface_weights_dir}'. Building WITHOUT bundled weights - the "
        f"shipped exe will try to download them from the internet on the "
        f"customer's machine the first time face capture/training runs. "
        f"Run the app once locally to populate this folder, or set the "
        f"DEEPFACE_WEIGHTS_DIR environment variable, then rebuild before "
        f"shipping to an offline client."
    )

# mediapipe ships compiled model files (palm/face detection .tflite,
# .binarypb graph configs) as package data - collect_data_files finds
# these; collect_dynamic_libs (below, in binaries) finds its separately
# compiled _framework_bindings extension, which is a distinct concern
# from the data files and is easy to miss.
datas += collect_data_files('mediapipe')

# tf_keras is TensorFlow 2.15's separate Keras-compatibility package
# (a distinct top-level import from `tensorflow` itself) - it ships its
# own data files and is NOT pulled in by collect_submodules('tensorflow')
# below, since it's a different package.
datas += collect_data_files('tf_keras')

# cv2.data.haarcascades and similar package-relative data paths (if used
# anywhere as a fallback face detector) live in cv2's own package data,
# which import tracing alone won't catch.
datas += collect_data_files('cv2')

# reportlab bundles its own font/AFM metric files as package data -
# needed for anything beyond core Helvetica/Times in generated payslips.
datas += collect_data_files('reportlab')

# pikepdf's own package data (alongside its compiled qpdf bindings,
# handled separately in binaries below).
datas += collect_data_files('pikepdf')

# APScheduler discovers its jobstore/trigger/executor plugins via
# pkg_resources/importlib.metadata ENTRY POINTS read from the package's
# installed .dist-info metadata - not via plain imports. PyInstaller
# does not bundle that metadata by default, so without this the frozen
# exe can fail at scheduler-init time even though every import
# succeeded. This is the single most common APScheduler + PyInstaller
# failure mode and is easy to miss because it doesn't show up until the
# scheduler actually tries to start.
datas += copy_metadata('APScheduler')

# ---------------------------------------------------------------------
# Binaries: compiled extension modules / shared libraries that some
# packages load indirectly (not via a plain `import`), which
# PyInstaller's dependency walker can fail to pick up on its own.
# ---------------------------------------------------------------------
binaries = []
binaries += collect_dynamic_libs('mediapipe')     # compiled _framework_bindings extension
binaries += collect_dynamic_libs('cryptography')  # Rust-backed hazmat bindings
binaries += collect_dynamic_libs('pikepdf')       # bundled qpdf shared library

# ---------------------------------------------------------------------
# Hidden imports: modules only reached dynamically (e.g. via
# importlib/plugin-style loading, or internal string-based registries),
# which PyInstaller's static import trace won't find on its own.
# ---------------------------------------------------------------------
hiddenimports = [
    # The app module itself - launcher.py now loads this BY NAME via
    # runpy.run_module('app', ...) rather than a plain top-level
    # `import app`, so it's listed explicitly here rather than relying
    # on PyInstaller's string-literal import detection to catch it.
    'app',

    # Flask & Web
    'flask',
    'jinja2',
    'werkzeug',
    'click',
    'itsdangerous',
    'flask_sqlalchemy',
    'flask_migrate',
    'flask_wtf',
    'wtforms',
    'flask_limiter',

    # Database & Security
    'sqlalchemy',
    'pymysql',
    'cryptography',
    'cryptography.fernet',
    '_cffi_backend',   # cryptography's compiled cffi backend, loaded indirectly

    # AI & Computer Vision
    'cv2',
    'deepface',
    'mediapipe',
    'tensorflow',
    'keras',
    'tf_keras',
    'numpy',
    'h5py',            # loads DeepFace's .h5 weight files; fragile on Windows

    # Utilities, Files & PDF
    'PIL',
    'reportlab',
    'qrcode',
    'pikepdf',
    'dotenv',
    'dateutil',
    'apscheduler',
]

# Auto-collect submodules for packages with dynamic/plugin-style internal
# loading that a flat top-level import doesn't fully cover.
hiddenimports += collect_submodules('tensorflow')
hiddenimports += collect_submodules('tf_keras')
hiddenimports += collect_submodules('mediapipe')
hiddenimports += collect_submodules('deepface')
hiddenimports += collect_submodules('cv2')
hiddenimports += collect_submodules('cryptography')
hiddenimports += collect_submodules('reportlab')
hiddenimports += collect_submodules('apscheduler')
hiddenimports += collect_submodules('flask_wtf')
hiddenimports += collect_submodules('flask_limiter')
hiddenimports += collect_submodules('flask_sqlalchemy')
hiddenimports += collect_submodules('h5py')

# SQLAlchemy resolves 'sqlite:///...' / 'mysql+pymysql://...' dialect
# strings through an internal registry, not a plain top-level import -
# without these, a frozen exe can fail at engine-creation time even
# though `import sqlalchemy` itself succeeded.
hiddenimports += collect_submodules('sqlalchemy.dialects.sqlite')
hiddenimports += collect_submodules('sqlalchemy.dialects.mysql')

# Flask-Limiter's underlying `limits` package selects its storage
# backend (memory:// here) through similar dynamic-lookup machinery.
hiddenimports += collect_submodules('limits')

hiddenimports += [
    'engineio.async_drivers.threading',  # common Flask/eventlet-adjacent gotcha
    'pkg_resources.py2_warn',
]

a = Analysis(
    ['launcher.py'],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim build size/time - these are dev-only, never needed at runtime.
        'pytest', 'pytest_flask',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AttendancePayrollSystem',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # smaller exe; set False first if you hit false-positive AV flags (see checklist)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # windowed app, no console popup - customer just sees the browser open.
                         # Flip to True TEMPORARILY on your own machine only, if you need to see
                         # console output while debugging a build issue - never ship console=True.
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,           # point at your own .ico file here before shipping
)

# ---------------------------------------------------------------------
# BUILD VERIFICATION CHECKLIST - do all of these before shipping to a
# customer, in this order:
#
# [x] requirements.txt: plain `opencv-python` has been removed - only
#     `opencv-contrib-python==4.8.1.78` remains. Re-create your build
#     venv from scratch (don't just `pip install -r requirements.txt`
#     on top of an old venv that may still have opencv-python installed)
#     to make sure the stale package is actually gone:
#       pip uninstall -y opencv-python opencv-contrib-python
#       pip install -r requirements.txt
# [ ] Populate DeepFace weights BEFORE building (run `python app.py`
#     locally and do one face capture) so the auto-detect block above
#     finds and bundles ~/.deepface/weights instead of printing the
#     "WARNING: ... building WITHOUT bundled weights" message.
# [ ] Build succeeds with no errors (`pyinstaller attendance_app.spec --clean`).
# [ ] Run the built exe from a folder OUTSIDE your dev environment (a
#     fresh temp folder, or better, a clean VM/second machine) - many
#     "works on my machine" packaging bugs only show up away from the
#     dev environment's installed Python/PATH.
# [ ] Confirm the browser auto-opens to http://127.0.0.1:5000 and the
#     login page renders (proves templates/static were bundled correctly).
# [ ] Confirm SQLite database creation works (first-run creates
#     instance/attendance.db) - the exe needs write permission in its own
#     folder; prefer installing to %LOCALAPPDATA%\AttendancePayrollSystem
#     rather than Program Files.
# [ ] Confirm the setup wizard runs (create first admin, set company info).

# [ ] Confirm the background scheduler actually starts without error -
#     this is where the APScheduler entry-point/metadata issue above
#     would surface, and it can fail silently in a way that looks like
#     the app started fine.
# [ ] Trigger an actual face capture/registration flow - this is where
#     DeepFace/TensorFlow/OpenCV/MediaPipe packaging problems surface.
#     Expect at least one round of "add this to hiddenimports/datas"
#     iteration here.
# [ ] Confirm PDF payslip generation (including AES-256 encryption via
#     pikepdf) and, if configured, email sending both work.

# [ ] Check Windows Defender / other AV doesn't flag the exe - try
#     upx=False if this happens, and consider code-signing the exe with
#     a purchased certificate, which meaningfully reduces false
#     positives and is close to mandatory for anything you charge for.
# ---------------------------------------------------------------------
