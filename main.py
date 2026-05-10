import logging
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from firestore_client import save_buyer, update_email_status
from emailer import send_worksheet_email

# ---------------------------------------------------------------------------
# Logging — structured format that Cloud Run captures cleanly
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App & Templates
# ---------------------------------------------------------------------------
try:
    app = FastAPI()
    templates = Jinja2Templates(directory="templates")
    logger.info("FastAPI app and Jinja2 templates initialised successfully")
except Exception:
    logger.critical("Failed to initialise FastAPI app or templates", exc_info=True)
    raise

# ---------------------------------------------------------------------------
# Book Metadata — single source of truth
# ---------------------------------------------------------------------------
BOOKS = {
    "book1": {
        "title": "Why Great Manufacturers Stay Invisible",
        "email_subject": "Your Worksheets — Why Great Manufacturers Stay Invisible",
    },
    "book2": {
        "title": "Stop Planning Start Winning",
        "email_subject": "Your Worksheets — Stop Planning Start Winning",
    },
    "book3": {
        "title": "Don't Bet the Business",
        "email_subject": "Your Worksheets — Don't Bet the Business",
    },
    "book4": {
        "title": "Decoding the Rs. 100 Cr Breakthrough",
        "email_subject": "Your Worksheets — Decoding the Rs. 100 Cr Breakthrough",
    },
}

# ---------------------------------------------------------------------------
# Startup: verify GCS bucket is reachable and all 4 worksheet blobs exist
# ---------------------------------------------------------------------------
@app.on_event("startup")
def verify_gcs_worksheets():
    from gcs_client import download_worksheet
    from google.cloud import storage
    import os

    bucket_name = os.environ.get("GCS_BUCKET_NAME", "sudharsankr-worksheets")
    logger.info(f"Startup check: verifying worksheet blobs in GCS | bucket={bucket_name}")

    all_ok = True
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        for book_id in BOOKS.keys():
            blob_name = f"{book_id}.pdf"
            try:
                blob = bucket.blob(blob_name)
                if blob.exists():
                    logger.info(f"  ✓ {book_id}: gs://{bucket_name}/{blob_name} found")
                else:
                    logger.error(f"  ✗ {book_id}: gs://{bucket_name}/{blob_name} MISSING — email will fail for this book")
                    all_ok = False
            except Exception as e:
                logger.error(f"  ✗ {book_id}: error checking blob {blob_name} | error={e}")
                all_ok = False
    except Exception:
        logger.error("Startup check failed — could not connect to GCS", exc_info=True)
        return

    if all_ok:
        logger.info("Startup check passed — all 4 worksheet blobs found in GCS")
    else:
        logger.warning("Startup check incomplete — one or more worksheet blobs missing from GCS")


# ---------------------------------------------------------------------------
# Health check — Cloud Run uses this to confirm the service is alive
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    logger.debug("Health check called")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Generic form server — used by all 4 GET routes
# ---------------------------------------------------------------------------
def serve_form(request: Request, book_id: str):
    """Serve the HTML registration form for a given book."""
    logger.info(f"Form requested | book_id={book_id} | ip={request.client.host}")
    try:
        book = BOOKS.get(book_id)
        if not book:
            logger.warning(f"Unknown book_id requested: {book_id}")
            return HTMLResponse("Page not found", status_code=404)

        return templates.TemplateResponse(
            "form.html",
            {"request": request, "book_id": book_id, "book_title": book["title"]},
        )
    except Exception:
        logger.error(f"Unexpected error serving form for book_id={book_id}", exc_info=True)
        return HTMLResponse("Something went wrong. Please try again.", status_code=500)


# ---------------------------------------------------------------------------
# Generic submission handler — used by all 4 POST routes
# ---------------------------------------------------------------------------
async def handle_submission(request: Request, book_id: str, name: str, email: str):
    """
    On form submit:
      1. Validate book exists
      2. Save buyer record to Firestore (email_sent=False initially)
      3. Send worksheet email via GoDaddy SMTP
      4. Update email_sent flag in Firestore
      5. Return thank you page or error page
    """
    logger.info(f"Form submitted | book_id={book_id} | email={email} | name={name}")

    # --- Validate book ---
    try:
        book = BOOKS.get(book_id)
        if not book:
            logger.warning(f"Submission for unknown book_id={book_id}")
            return HTMLResponse("Page not found", status_code=404)
    except Exception:
        logger.error("Unexpected error during book lookup", exc_info=True)
        return HTMLResponse("Something went wrong. Please try again.", status_code=500)

    # --- Step 1: Save to Firestore ---
    firestore_ok = False
    doc_id = None
    try:
        doc_id = save_buyer(name=name, email=email, book=book_id)
        firestore_ok = True
        logger.info(f"Firestore save success | doc_id={doc_id} | email={email} | book_id={book_id}")
    except ValueError as e:
        logger.error(f"Firestore validation error | email={email} | book_id={book_id} | error={e}")
    except ConnectionError as e:
        logger.error(f"Firestore connection error | email={email} | book_id={book_id} | error={e}")
    except Exception:
        logger.error(f"Firestore write failed | email={email} | book_id={book_id}", exc_info=True)

    # --- Step 2: Send email ---
    email_ok = False
    try:
        send_worksheet_email(
            to_name=name,
            to_email=email,
            subject=book["email_subject"],
            book_title=book["title"],
            book_id=book_id,
        )
        email_ok = True
        logger.info(f"Email sent successfully | email={email} | book_id={book_id}")
    except FileNotFoundError as e:
        logger.error(f"Worksheet PDF not found in GCS | book_id={book_id} | error={e}")
    except ConnectionError as e:
        logger.error(f"SMTP connection failed | email={email} | book_id={book_id} | error={e}")
    except Exception:
        logger.error(f"Email send failed | email={email} | book_id={book_id}", exc_info=True)

    # --- Step 3: Update email_sent flag in Firestore ---
    if firestore_ok and doc_id:
        try:
            update_email_status(doc_id, email_ok)
            logger.info(f"Firestore email_sent updated | doc_id={doc_id} | email_sent={email_ok}")
        except Exception:
            # Non-critical — don't affect user response
            logger.error(f"Failed to update email_sent flag | doc_id={doc_id}", exc_info=True)

    # --- Step 4: Respond to reader ---
    if email_ok:
        logger.info(f"Returning thank you page | email={email} | book_id={book_id}")
        try:
            return templates.TemplateResponse(
                "thankyou.html",
                {"request": request, "name": name, "book_title": book["title"]},
            )
        except Exception:
            logger.error("Failed to render thankyou.html", exc_info=True)
            return HTMLResponse("Your worksheets have been sent. Check your inbox!", status_code=200)
    else:
        logger.warning(f"Returning error page | email={email} | book_id={book_id}")
        try:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "book_id": book_id, "book_title": book["title"]},
                status_code=500,
            )
        except Exception:
            logger.error("Failed to render error.html", exc_info=True)
            return HTMLResponse(
                "We could not send your worksheets. Please try again.",
                status_code=500,
            )


# ---------------------------------------------------------------------------
# Book 1 Routes
# ---------------------------------------------------------------------------
@app.get("/book1", response_class=HTMLResponse)
def book1_form(request: Request):
    return serve_form(request, "book1")


@app.post("/book1", response_class=HTMLResponse)
async def book1_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
):
    return await handle_submission(request, "book1", name, email)


# ---------------------------------------------------------------------------
# Book 2 Routes
# ---------------------------------------------------------------------------
@app.get("/book2", response_class=HTMLResponse)
def book2_form(request: Request):
    return serve_form(request, "book2")


@app.post("/book2", response_class=HTMLResponse)
async def book2_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
):
    return await handle_submission(request, "book2", name, email)


# ---------------------------------------------------------------------------
# Book 3 Routes
# ---------------------------------------------------------------------------
@app.get("/book3", response_class=HTMLResponse)
def book3_form(request: Request):
    return serve_form(request, "book3")


@app.post("/book3", response_class=HTMLResponse)
async def book3_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
):
    return await handle_submission(request, "book3", name, email)


# ---------------------------------------------------------------------------
# Book 4 Routes
# ---------------------------------------------------------------------------
@app.get("/book4", response_class=HTMLResponse)
def book4_form(request: Request):
    return serve_form(request, "book4")


@app.post("/book4", response_class=HTMLResponse)
async def book4_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
):
    return await handle_submission(request, "book4", name, email)
