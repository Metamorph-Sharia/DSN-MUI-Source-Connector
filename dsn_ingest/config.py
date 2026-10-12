"""Pipeline configuration."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "dsn"
BLOB_DIR = DATA_DIR / "blobs"
TEXT_DIR = DATA_DIR / "text"
DB_PATH = DATA_DIR / "dsn_fatwa.db"

BASE_URL = "https://dsnmui.or.id"
FATWA_LIST_URL = f"{BASE_URL}/kategori/fatwa/"


def page_url(n: int) -> str:
    """1 → /kategori/fatwa/, 2 → /kategori/fatwa/page/2/, ..."""
    return FATWA_LIST_URL if n == 1 else f"{BASE_URL}/kategori/fatwa/page/{n}/"


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

HTML_TIMEOUT_S = 30
FILE_TIMEOUT_S = 120

# fallback ladder tuning (see fetcher.py)
MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0          # 2s, 4s, 8s...
DELAY_BETWEEN_FILES_S = 1.0   # politeness between downloads

# bot interstitial ("One moment, please…", JS self-reload):
# appears intermittently (volume-triggered). Plain retries sometimes clear it;
# if not, discovery degrades gracefully (connector falls back to stored index).
GATE_RETRIES = 3
GATE_WAIT_S = 20
INTERSTITIAL_TITLES = {"one moment, please...", "mohon tunggu sebentar…"}

DEFAULT_MAX_PAGES = 30        # corpus was 17 pages (166 fatwas) Oct 2026

# the listing changes a few times a year; re-crawling it on every sync is what
# trips the gate. sync reuses the stored index while it is younger than this.
INDEX_MAX_AGE_S = 24 * 3600
