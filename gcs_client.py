import logging
import os

from google.cloud import storage
from google.api_core.exceptions import NotFound, Forbidden, GoogleAPICallError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GCS config — bucket name from environment variable
# Defaults to "sudharsankr-worksheets" if not set
# ---------------------------------------------------------------------------
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "sudharsankr-worksheets")

if not GCS_BUCKET_NAME:
    logger.warning("GCS_BUCKET_NAME environment variable is not set — PDF downloads will fail")
else:
    logger.info(f"GCS config loaded | bucket={GCS_BUCKET_NAME}")

# ---------------------------------------------------------------------------
# GCS client — initialised once at module load
# On Cloud Run, authenticates automatically via the attached service account.
# Requires IAM role: roles/storage.objectViewer on the bucket.
# ---------------------------------------------------------------------------
try:
    _storage_client = storage.Client()
    logger.info("Google Cloud Storage client initialised successfully")
except Exception:
    logger.critical("Failed to initialise Google Cloud Storage client", exc_info=True)
    raise


def download_worksheet(book_id: str) -> bytes:
    """
    Download the worksheet PDF for a given book_id from the private GCS bucket.
    Returns the PDF as bytes — never written to disk.
    Raises an exception on failure — caller handles it.

    Expected blob names in bucket:
      book1.pdf, book2.pdf, book3.pdf, book4.pdf

    Failure modes handled:
      - ValueError: empty or invalid book_id
      - google.api_core.exceptions.NotFound: blob does not exist in bucket
      - google.api_core.exceptions.Forbidden: service account lacks permissions
      - google.api_core.exceptions.GoogleAPICallError: any GCS API error
      - Exception: unexpected failures
    """
    if not book_id:
        raise ValueError("book_id must not be empty")

    blob_name = f"{book_id}.pdf"
    logger.info(f"Downloading worksheet from GCS | bucket={GCS_BUCKET_NAME} | blob={blob_name}")

    try:
        bucket = _storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        pdf_bytes = blob.download_as_bytes()
        logger.info(
            f"Worksheet downloaded successfully | bucket={GCS_BUCKET_NAME} "
            f"| blob={blob_name} | size={len(pdf_bytes)} bytes"
        )
        return pdf_bytes

    except NotFound:
        logger.error(
            f"Worksheet blob not found in GCS | bucket={GCS_BUCKET_NAME} | blob={blob_name}"
        )
        raise FileNotFoundError(
            f"Worksheet '{blob_name}' not found in bucket '{GCS_BUCKET_NAME}'"
        )

    except Forbidden:
        logger.error(
            f"Permission denied accessing GCS bucket | bucket={GCS_BUCKET_NAME} | blob={blob_name} "
            f"— ensure Cloud Run service account has roles/storage.objectViewer"
        )
        raise PermissionError(
            f"Access denied to bucket '{GCS_BUCKET_NAME}' — check IAM permissions"
        )

    except GoogleAPICallError as e:
        logger.error(
            f"GCS API error downloading worksheet | bucket={GCS_BUCKET_NAME} "
            f"| blob={blob_name} | error={e}"
        )
        raise

    except Exception:
        logger.error(
            f"Unexpected error downloading worksheet | bucket={GCS_BUCKET_NAME} "
            f"| blob={blob_name}",
            exc_info=True,
        )
        raise
