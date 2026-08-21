import os
from datetime import timedelta
from dotenv import load_dotenv

# Load .env from the project directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, '.env')
load_dotenv(ENV_FILE)

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-change-in-production'
    
    # Database Configuration
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///attendance.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Session Configuration
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Upload Configuration
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
    DATASET_FOLDER = os.path.join(os.path.dirname(__file__), 'dataset')
    TRAINED_MODEL_FOLDER = os.path.join(os.path.dirname(__file__), 'trained_model')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    
    # Allowed Extensions
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    
    # Email Configuration (SMTP)
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')
    
    # Company Settings
    COMPANY_NAME = os.environ.get('COMPANY_NAME') or 'AI Attendance System'
    COMPANY_LOGO = os.environ.get('COMPANY_LOGO') or 'static/images/company_logo.png'
    
    # Office Timing
    OFFICE_START_TIME = os.environ.get('OFFICE_START_TIME') or '09:00'
    OFFICE_END_TIME = os.environ.get('OFFICE_END_TIME') or '18:00'
    GRACE_PERIOD_MINUTES = int(os.environ.get('GRACE_PERIOD_MINUTES') or 15)
    
    # Working Hours
    WORKING_HOURS_PER_DAY = float(os.environ.get('WORKING_HOURS_PER_DAY') or 9.0)
    
    # Salary Calculation
    LATE_DEDUCTION_ENABLED = os.environ.get('LATE_DEDUCTION_ENABLED', 'false').lower() in ['true', 'on', '1']
    LATE_DEDUCTION_PER_OCCURRENCE = float(os.environ.get('LATE_DEDUCTION_PER_OCCURRENCE') or 0.0)
    
    # Overtime Calculation
    OVERTIME_ENABLED = os.environ.get('OVERTIME_ENABLED', 'true').lower() in ['true', 'on', '1']
    OVERTIME_RATE = float(os.environ.get('OVERTIME_RATE') or 1.5)
    
    # Face Recognition Settings
    FACE_RECOGNITION_TOLERANCE = float(os.environ.get('FACE_RECOGNITION_TOLERANCE') or 0.6)
    MIN_FACE_IMAGES_REQUIRED = int(os.environ.get('MIN_FACE_IMAGES_REQUIRED') or 20)
    
    # Security
    CSRF_ENABLED = True
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
