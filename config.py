import os
from dotenv import load_dotenv

# Muat variabel dari file .env
load_dotenv()

# === DATABASE ===
DATABASE_URI = os.getenv('DATABASE_URI', 'sqlite:///ojs_pipeline.db')

# === REDIS ===
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

# === AI (KoboldCPP) ===
KOBOLDCPP_URL = os.getenv('KOBOLDCPP_URL', 'http://localhost:5001/api/v1/generate')

# === OJS Output Settings ===
# Locale digunakan untuk atribut XML (contoh: 'id_ID' atau 'en')
OJS_LOCALE     = os.getenv('OJS_LOCALE', 'id_ID')
OJS_SECTION_ID = int(os.getenv('OJS_SECTION_ID', 1))

# === OJS Automation Settings ===
OJS_LOGIN_URL = os.getenv('OJS_LOGIN_URL', 'http://localhost/ojs/index.php/journal/login')
OJS_USERNAME  = os.getenv('OJS_USERNAME', 'admin')
OJS_PASSWORD  = os.getenv('OJS_PASSWORD', 'admin123')
