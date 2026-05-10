import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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
# SMTP config — all values injected via Cloud Run environment variables
# ---------------------------------------------------------------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "smtpout.secureserver.net")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)

# Warn at startup if credentials are missing
if not SMTP_USER or not SMTP_PASS:
    logger.warning("SMTP_USER or SMTP_PASS environment variable is not set — email sending will fail")
else:
    logger.info(f"SMTP config loaded | host={SMTP_HOST} | port={SMTP_PORT} | from={EMAIL_FROM}")


def _build_email_body(to_name: str, book_title: str) -> str:
    """Plain text email body — warm, minimal, on-brand."""
    return f"""Hi {to_name},

Thank you for reading {book_title}.

Please find your worksheets attached to this email. Work through them at your own pace — they are designed to help you apply the ideas directly to your business.

If you have any questions, just reply to this email.

Warm regards,
Sudharsan K R
The Manufacturing Strategy Series
"""


def _load_pdf(book_id: str) -> bytes:
    """
    Download the worksheet PDF from GCS bucket into memory.
    Never written to disk — bytes go straight into the email attachment.
    Raises FileNotFoundError if the blob is missing in GCS.
    """
    from gcs_client import download_worksheet
    try:
        data = download_worksheet(book_id)
        logger.info(f"PDF loaded from GCS successfully | book_id={book_id} | size={len(data)} bytes")
        return data
    except FileNotFoundError:
        logger.error(f"Worksheet not found in GCS | book_id={book_id}")
        raise
    except PermissionError:
        logger.error(f"Permission denied fetching worksheet from GCS | book_id={book_id}")
        raise
    except Exception:
        logger.error(f"Failed to load PDF from GCS | book_id={book_id}", exc_info=True)
        raise


def _build_message(
    to_name: str,
    to_email: str,
    subject: str,
    book_title: str,
    worksheet_path: str,
    pdf_data: bytes,
) -> MIMEMultipart:
    """Build the MIME email message with body and PDF attachment."""
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM
        msg["To"] = to_email
        msg["Subject"] = subject

        # Plain text body
        body = _build_email_body(to_name, book_title)
        msg.attach(MIMEText(body, "plain"))

        # PDF attachment
        attachment = MIMEApplication(pdf_data, _subtype="pdf")
        filename = os.path.basename(worksheet_path)
        attachment.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(attachment)

        logger.info(f"Email message built | to={to_email} | subject={subject} | attachment={filename}")
        return msg

    except Exception:
        logger.error(f"Failed to build email message | to={to_email}", exc_info=True)
        raise


def send_worksheet_email(
    to_name: str,
    to_email: str,
    subject: str,
    book_title: str,
    book_id: str,
) -> None:
    """
    Send the worksheet PDF to the reader via GoDaddy SMTP (SSL, port 465).
    PDF is downloaded from GCS bucket into memory — never written to disk.
    Raises an exception on any failure — caller handles it.

    Failure modes handled:
      - FileNotFoundError: worksheet PDF missing from GCS bucket
      - PermissionError: service account lacks GCS read permission
      - smtplib.SMTPAuthenticationError: wrong SMTP credentials
      - smtplib.SMTPRecipientsRefused: invalid recipient email
      - smtplib.SMTPConnectError: cannot reach SMTP server
      - smtplib.SMTPException: any other SMTP protocol error
      - OSError / TimeoutError: network-level failures
    """
    logger.info(f"Preparing to send email | to={to_email} | book_id={book_id}")

    # --- Download PDF from GCS into memory ---
    pdf_data = _load_pdf(book_id)

    # --- Build MIME message ---
    msg = _build_message(
        to_name=to_name,
        to_email=to_email,
        subject=subject,
        book_title=book_title,
        worksheet_path=f"{book_id}.pdf",
        pdf_data=pdf_data,
    )

    # --- Connect and send via GoDaddy SMTP ---
    logger.info(f"Connecting to SMTP server | host={SMTP_HOST} | port={SMTP_PORT}")
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            logger.info("SMTP connection established")

            try:
                server.login(SMTP_USER, SMTP_PASS)
                logger.info(f"SMTP login successful | user={SMTP_USER}")
            except smtplib.SMTPAuthenticationError as e:
                logger.error(f"SMTP authentication failed | user={SMTP_USER} | error={e}")
                raise

            try:
                server.sendmail(EMAIL_FROM, to_email, msg.as_string())
                logger.info(f"Email sent successfully | to={to_email} | from={EMAIL_FROM}")
            except smtplib.SMTPRecipientsRefused as e:
                logger.error(f"Recipient refused by SMTP server | to={to_email} | error={e}")
                raise
            except smtplib.SMTPSenderRefused as e:
                logger.error(f"Sender refused by SMTP server | from={EMAIL_FROM} | error={e}")
                raise
            except smtplib.SMTPDataError as e:
                logger.error(f"SMTP data error while sending | to={to_email} | error={e}")
                raise

    except smtplib.SMTPConnectError as e:
        logger.error(f"Cannot connect to SMTP server | host={SMTP_HOST} | port={SMTP_PORT} | error={e}")
        raise ConnectionError(f"SMTP connection failed: {e}") from e
    except smtplib.SMTPException as e:
        logger.error(f"SMTP protocol error | to={to_email} | error={e}", exc_info=True)
        raise
    except (OSError, TimeoutError) as e:
        logger.error(f"Network error during SMTP send | to={to_email} | error={e}")
        raise ConnectionError(f"Network error during email send: {e}") from e
    except Exception:
        logger.error(f"Unexpected error during email send | to={to_email}", exc_info=True)
        raise

