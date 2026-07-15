import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.getenv("AICRAFT_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("AICRAFT_DATABASE_URL", f"sqlite:///{DATA_DIR / 'aicraft.db'}")

# --- Google Sheet (Reference Sync, lettura + mark dei contenuti scaricati) ---
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TABS = [
    t.strip() for t in os.getenv("GOOGLE_SHEET_TABS", "CAROSELLI,VIRAL GENERAL").split(",") if t.strip()
]
GOOGLE_SHEET_MARK_DOWNLOADS = os.getenv("GOOGLE_SHEET_MARK_DOWNLOADS", "1").lower() not in ("0", "false", "no")
GOOGLE_SHEET_CAROUSEL_MARK_COLOR = os.getenv("GOOGLE_SHEET_CAROUSEL_MARK_COLOR", "1.0,0.95,0.65")

# --- Instagram (download reference) ---
# Nessuna password: la sessione viene importata dai cookie del browser locale
# (via browser_cookie3), riusando il login gia' fatto a mano su instagram.com.
INSTAGRAM_BROWSER = os.getenv("INSTAGRAM_BROWSER", "chrome")
INSTAGRAM_SESSION_DIR = Path(os.getenv("AICRAFT_IG_SESSION_DIR", DATA_DIR / "ig_sessions"))
INSTAGRAM_SESSION_DIR.mkdir(parents=True, exist_ok=True)

MEDIA_DIR = Path(os.getenv("AICRAFT_MEDIA_DIR", DATA_DIR / "media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Libreria reference locale: si pesca dalle ultime N settimane disponibili,
# partendo dalla piu' vecchia dentro la finestra. I file/reference IG oltre
# retention vengono rimossi; gli asset generati/consegnati restano intatti.
REFERENCE_SELECTION_WEEKS = int(os.getenv("AICRAFT_REFERENCE_SELECTION_WEEKS", "2"))
REFERENCE_RETENTION_DAYS = int(os.getenv("AICRAFT_REFERENCE_RETENTION_DAYS", "45"))
REFERENCE_SYNC_MAX_ITEMS = int(os.getenv("AICRAFT_REFERENCE_SYNC_MAX_ITEMS", "25"))
REFERENCE_SYNC_POLICY = os.getenv(
    "AICRAFT_REFERENCE_SYNC_POLICY",
    "CAROSELLI:BOOBS=5,CAROSELLI:BOOTY=5,CAROSELLI:GENERAL=5,"
    "VIRAL GENERAL:TALKING=5,VIRAL GENERAL:BALLETTI/LIPSYNC=5,VIRAL GENERAL:CAPTION=5",
)

# --- Whisper (trascrizione locale) ---
WHISPER_MODEL_SIZE = os.getenv("AICRAFT_WHISPER_MODEL", "small")

# --- Production Engine ---
DELIVERY_DIR = Path(os.getenv("AICRAFT_DELIVERY_DIR", DATA_DIR / "delivery"))
DELIVERY_DIR.mkdir(parents=True, exist_ok=True)

WORK_DIR = Path(os.getenv("AICRAFT_WORK_DIR", DATA_DIR / "production_work"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

# Binari esterni invocati via subprocess (nomi verificati sulla documentazione
# ufficiale, non ancora testati contro un account reale: vedi
# docs/ai-craft-architecture.md §7 per lo stato).
HIGGSFIELD_CLI_BIN = os.getenv("AICRAFT_HIGGSFIELD_BIN", "higgsfield")
CLAUDE_CLI_BIN = os.getenv("AICRAFT_CLAUDE_BIN", "claude")
