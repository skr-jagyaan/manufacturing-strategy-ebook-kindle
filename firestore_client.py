import logging
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import firestore
from google.api_core.exceptions import GoogleAPICallError, RetryError

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
# Initialise Firebase Admin SDK once at module load
# On Cloud Run, authenticates automatically via the attached service account.
# Requires IAM role: roles/datastore.user on the GCP project.
# ---------------------------------------------------------------------------
try:
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
        logger.info("Firebase Admin SDK initialised successfully")
    else:
        logger.info("Firebase Admin SDK already initialised — reusing existing app")
except Exception:
    logger.critical("Failed to initialise Firebase Admin SDK", exc_info=True)
    raise

try:
    db = firestore.client()
    logger.info("Firestore client created successfully")
except Exception:
    logger.critical("Failed to create Firestore client", exc_info=True)
    raise

COLLECTION = "kindle_buyers"


def save_buyer(name: str, email: str, book: str) -> str:
    """
    Save a new buyer document to the kindle_buyers Firestore collection.
    Sets email_sent=False initially — updated after email attempt.
    Returns the auto-generated document ID.
    Raises an exception on failure — caller handles it.
    """
    logger.info(f"Saving buyer to Firestore | email={email} | book={book}")

    if not name or not email or not book:
        raise ValueError(f"Missing required fields: name={name}, email={email}, book={book}")

    try:
        doc_ref = db.collection(COLLECTION).document()
        doc_ref.set({
            "name": name,
            "email": email,
            "book": book,
            "timestamp": datetime.now(timezone.utc),
            "email_sent": False,
        })
        logger.info(f"Firestore document created | doc_id={doc_ref.id} | email={email} | book={book}")
        return doc_ref.id

    except GoogleAPICallError as e:
        logger.error(f"Firestore API error while saving buyer | email={email} | book={book} | error={e}")
        raise
    except RetryError as e:
        logger.error(f"Firestore retry exhausted while saving buyer | email={email} | book={book} | error={e}")
        raise
    except Exception:
        logger.error(f"Unexpected error saving buyer to Firestore | email={email} | book={book}", exc_info=True)
        raise


def update_email_status(doc_id: str, success: bool) -> None:
    """
    Update the email_sent flag on an existing buyer document.
    Called after the SMTP send attempt — records whether delivery succeeded.
    Raises an exception on failure — caller handles it (non-critical path).
    """
    logger.info(f"Updating email_sent flag | doc_id={doc_id} | email_sent={success}")

    if not doc_id:
        raise ValueError(f"Invalid doc_id: {doc_id}")

    try:
        db.collection(COLLECTION).document(doc_id).update({"email_sent": success})
        logger.info(f"email_sent flag updated successfully | doc_id={doc_id} | email_sent={success}")

    except GoogleAPICallError as e:
        logger.error(f"Firestore API error updating email_sent | doc_id={doc_id} | error={e}")
        raise
    except RetryError as e:
        logger.error(f"Firestore retry exhausted updating email_sent | doc_id={doc_id} | error={e}")
        raise
    except Exception:
        logger.error(f"Unexpected error updating email_sent | doc_id={doc_id}", exc_info=True)
        raise

