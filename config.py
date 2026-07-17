import os

class Config:
    # GitHub Configuration
    GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
    
    # Firebase Configuration
    FIREBASE_CREDENTIALS = os.getenv('FIREBASE_SERVICE_ACCOUNT', '')
    
    # App Configuration
    SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key-here')
    
    # Other settings
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    COMPILE_TIMEOUT = int(os.getenv('COMPILE_TIMEOUT', 30))
