# LedgerLens — Project Handoff for Continued Development

Status checked: 17 September 2026.

## 1. What I want to build and how I want help

I am building an AI-powered accounts payable application named LedgerLens. The current work focuses on invoice upload, OCR processing, and displaying extracted invoice details.

I now want to continue implementing it myself with ChatGPT guiding me. Read the existing code before suggesting changes. Explain one manageable task at a time, give the expected result and verification command, and wait for my output before moving on. Preserve the existing frontend, authentication, stored invoices, API contracts, and digital-PDF processing.

My intended OCR workflow is:

```text
Invoice PDF / JPG / PNG
    -> Upload and validation
    -> Private document storage and background job
    -> Digital PDF text extraction OR scanned-page rendering
    -> Image preprocessing and PaddleOCR
    -> Text, confidence, coordinates and layout
    -> Database storage and status updates
    -> AI extraction module
```

My intended stack is Python, FastAPI, PaddleOCR, PyMuPDF, OpenCV, PostgreSQL, Redis and Celery. I want to integrate an existing OCR engine, not train a recognition model from scratch.

## 2. Current verified environment versus intended deployment

| Component | Current state |
|---|---|
| Development OS / shell | Windows / PowerShell |
| Python in `.venv` | 3.13.1 |
| Backend | FastAPI |
| Frontend | Plain HTML, CSS and JavaScript served by FastAPI |
| Database currently configured | SQLite: `data/ocr.db` |
| File storage currently configured | Local private files: `data/objects` |
| OCR provider currently configured | `tesseract` |
| OCR fallback currently configured | `tesseract` |
| PaddlePaddle package | Not installed at this check |
| PaddleOCR package | Not installed at this check |
| PostgreSQL / Redis / Celery / MinIO | Docker configuration exists; complete Docker deployment has not been verified |
| Digital PDF processing | Previously tested successfully on actual uploaded PDFs |
| Native scanned-document OCR | Not yet verified end to end |

Previously, Tesseract was unavailable and Docker was not running. Recheck their current availability rather than assuming that remains unchanged. The current code contains OCR adapters, but that does not mean their native engines are installed or validated.

This is a working development application with additional integrations prepared. It is not a fully validated production deployment. There is no Node frontend build process and no need for `npm run dev`.

## 3. Repository map

```text
app/
  main.py              FastAPI application, document/OCR APIs, static frontend
  config.py            Environment settings through Pydantic Settings
  auth.py              Administrator login, password verification, sessions
  db.py                SQLAlchemy models and database initialization
  validation.py        PDF/image validation and resource limits
  storage.py           Local/S3 storage and optional ClamAV scanning
  ocr.py               PDF routing, rendering, preprocessing and OCR orchestration
  engine_cli.py        Tesseract/PaddleOCR adapters run in child processes
  jobs.py              Job claims, progress, retries and result persistence
  worker.py            Local polling worker and Celery tasks/dispatcher
  invoice_summary.py   Rule-based invoice field extraction using text and coordinates
  static/
    index.html         Login and invoice workspace
    style.css          Responsive application styling
    app.js             Login, upload, polling, invoice grid and result display
tests/
  conftest.py           Isolated test database/storage configuration
  test_auth.py          Login, sessions, expiry, revocation and origin checks
  test_ocr.py           APIs, digital PDFs, validation and worker behavior
  test_invoice_summary.py  Field extraction and column-separation regressions
  test_native_ocr.py    Optional native Tesseract integration test
requirements.txt
requirements-paddle.txt
Dockerfile
compose.yaml
.env.example
.env                   Local configuration; do not share credentials
README.md
```

`requirements-paddle.txt` includes the base requirements with `-r requirements.txt`, then adds `paddlepaddle>=3.0,<4` and `paddleocr>=3.0,<4`. These ranges are not a fully pinned dependency lockfile.

## 4. Authentication already built

- A normal Name / Password login replaces the original API-key screen.
- The configured administrator username is `Admin`; the password was chosen by the owner and is deliberately omitted from this handoff.
- Password verification uses a salted PBKDF2-SHA256 hash with 600,000 iterations.
- Successful login creates an opaque session token; only its hash is stored in the database.
- The browser receives an HttpOnly, SameSite=Strict cookie named `ledgerlens_session`.
- Session duration defaults to eight hours.
- Sign-out revokes the database session and removes the cookie.
- Credential/organization changes invalidate existing sessions through a fingerprint check.
- Browser login/logout and authenticated write requests use `X-Requested-With: LedgerLens`; supplied origins must match the application origin.
- API keys remain an optional integration mechanism, but are currently disabled with `API_KEYS={}`.
- The administrator is mapped to `demo-organization`, where existing uploaded documents remain.

This is a configuration-based administrator account, not a full multi-user account-management system. Public deployment would still require operational hardening such as HTTPS, rate limiting and secret management.

## 5. Upload, validation and storage

The application accepts PDF, JPG, JPEG and PNG files. Defaults are 10 MB per file, 50 PDF pages, and 25 million image pixels.

Validation checks file extension, actual contents, empty files, file size, image decoding, image dimensions, and PDF integrity. Password-protected PDFs, repaired/damaged PDFs and animated images are rejected. Workers revalidate the stored document and check its SHA-256 before extraction.

Content hashes deduplicate uploads within each organization. Uploading the same document again returns the existing record and job. Separate organizations can hold their own copy of the same content.

Storage supports local files or an S3-compatible backend. Docker is configured with private MinIO storage. Original-document downloads use signed URLs valid for five minutes. Optional ClamAV scanning is implemented and fails closed when configured, but the default stack does not run a scanner. At-rest encryption is configuration/infrastructure dependent, not guaranteed by local development mode.

## 6. OCR pipeline already written

Each PDF page is evaluated independently:

- Pages with useful selectable text and limited raster coverage use PyMuPDF directly.
- Pages without useful text, or with substantial raster coverage, are rendered into bounded-size RGB images and passed to OCR.
- Mixed documents can therefore use direct extraction on some pages and OCR on others.
- JPG/PNG files go directly through image processing and OCR.

Preprocessing includes EXIF orientation correction, RGB conversion, resizing, grayscale conversion and CLAHE contrast enhancement. Laplacian variance estimates sharpness. A dedicated sharpening or deskew implementation has not been added.

`engine_cli.py` contains two adapters:

- Tesseract uses `pytesseract.image_to_data` and returns word text, confidence, bounding boxes and line identifiers.
- PaddleOCR uses its 3.x `predict` API and reads `rec_texts`, `rec_scores` and `rec_polys`. It returns recognized line regions, not necessarily individual words.

Paddle's document orientation classifier, document unwarping and text-line orientation options are currently disabled. Do not assume rotated scans are fully handled merely because EXIF orientation is supported.

Providers run as subprocesses with timeouts. The configured fallback is attempted if the primary provider fails. The default per-provider timeout is 90 seconds. A Paddle model is currently constructed in each provider subprocess, which may be slow for multipage documents; model caching and warm workers are potential later improvements.

The page result contains text, recognized regions, confidence, bounds, dimensions, engine/version, processing time, warnings, quality flags and table information.

Coordinates differ deliberately:

- Digital PDF: unrotated page coordinates in points.
- OCR image: coordinates in pixels of the oriented/resized image described by the page dimensions.

Digital extraction confidence is `null`, since no OCR prediction was made. OCR scores are engine estimates, not guarantees. Blank/low-confidence/blurry pages are flagged for human review.

PyMuPDF extracts digital table rows where detected. Scanned images have heuristic ruled-table-region detection; robust borderless tables, merged cells and semantic line-item extraction are not implemented.

## 7. Jobs, statuses and persistence

SQLAlchemy tables include:

- `documents`: owner organization, filename/type/size, object path, hash, page count, stage and timestamp.
- `ocr_jobs`: one job per document, attempts, progress, retry time, lease/run token, errors, confidence, warnings, timing and stage history.
- `ocr_results`: one row per document page, text, engine metadata and full page JSON.
- `login_sessions`: session-token hashes, organization, expiry and credential fingerprint.

Initial tables are created with SQLAlchemy `create_all`. There is no versioned migration framework yet.

Two worker modes exist:

1. `python -m app.worker`: sequential local polling of durable SQL jobs.
2. Celery worker plus Redis and Celery beat: the dispatcher scans due SQL jobs every five seconds.

Accepted jobs are durable database records, so acceptance does not depend on immediate broker availability. Job processing includes atomic claiming, lease renewal, unique run tokens, duplicate-delivery protection, expired-lease recovery, exponential retry delays and manual retry for failed jobs. These mechanisms exist in code; they have not been load-tested in the full distributed deployment.

Defaults are three attempts and a 900-second lease. Celery whole-task limits are 720 seconds soft / 780 seconds hard. Large or slow documents may exceed those limits and need further tuning or a different task structure.

Normal document history:

```text
UPLOADED -> VALIDATING -> OCR_PROCESSING -> OCR_COMPLETED
    -> AI_EXTRACTION_PENDING
```

Quality flags instead end in `HUMAN_REVIEW`. Transient errors use `OCR_RETRY_PENDING`; terminal failures use `OCR_FAILED`. Separate job status values are `queued`, `processing`, `retrying`, `completed`, and `failed`.

Progress is saved after each page. Complete result rows become available atomically when all pages finish. There is no partial completed-result API.

## 8. Invoice field detection and its latest fixes

`invoice_summary.py` derives unverified display fields from OCR results. This is rule-based extraction, not an LLM or trained invoice-understanding model.

Fields include customer, final amount, vendor/from, invoice number, invoice date, due date, subtotal, tax, balance, currency, phone, email, tax ID and purchase order.

Originally, flattened PDF text joined separate columns, producing errors such as:

```text
Invoice number: INV/26-27/757Nice Fashion LTD.
Invoice date: & Time : 10-Sep-2026Contact No.:
```

The detector now groups recognized regions by line identity, uses bounding boxes, splits large horizontal gaps, and pairs labels with values in the same row or closely aligned below. This separates customer details from the invoice-number/date column.

Dates are validated and stripped of appended time/contact text. Invoice identifiers are constrained rather than accepting an entire adjacent sentence. Final/grand/net/invoice-total labels take precedence over generic totals. Received amounts and balances do not substitute for the invoice total. Email and phone values receive basic format validation. Missing or conflicting candidates return `null` rather than selecting an arbitrary value.

Actual stored sample verified after the fix:

```text
Customer:       Nice Fashion LTD.
Invoice number: INV/26-27/757
Invoice date:   10-Sep-2026
Final amount:   ₹4,860.00
Subtotal:      ₹4,347.46
Balance:       ₹2,860.00
```

The stored sample's supplier remains undetected because it is not explicitly labeled in a supported way. The generic contact field may use a supplier phone when no customer contact is available; separate contact ownership is not modeled. These are known limitations, not verified customer/accounting data.

Summaries are computed from saved page results when the API is read. Improvements apply to existing documents without re-uploading or rerunning OCR. Original extracted text is preserved and may still contain the original reading-order artifacts.

## 9. Current user interface

- LedgerLens branding and a normal administrator sign-in form.
- The large introductory Invoice Processing banner was removed at the owner's request.
- Metrics for document count, queued/processing work, completed OCR and review flags.
- Drag/drop or file selection for uploads.
- A paginated invoice grid with document, date, invoice number, vendor, customer, phone, amount, tax, balance, processing status and View action.
- Search is limited to the currently loaded page; filters select all documents, completed OCR or review-required documents on that page.
- Document selection, progress history, warnings and failed-job retry.
- Extracted Text tab with page selection.
- Basic Details tab replacing the old coordinate/layout table. It shows invoice-wide values across all pages and hides page selection while active.
- Missing basic details display “Not detected”; grid cells use a dash.
- Original-document download and JSON export.
- Polling approximately every 2.5 seconds while signed in.

There are no invoice editing, payment execution, approval, or human-review assignment workflows yet. Processing statuses are not payment statuses.

## 10. API contract

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/v1/auth/login` | Authenticate using JSON username/password |
| GET | `/api/v1/auth/session` | Check browser session |
| POST | `/api/v1/auth/logout` | Revoke session |
| POST | `/api/v1/documents` | Multipart upload and automatic job creation |
| GET | `/api/v1/documents?limit=50&offset=0` | Organization-scoped documents and computed summaries |
| POST | `/api/v1/ocr/process` | Idempotent start or retry failed document |
| GET | `/api/v1/ocr/status/{document_id}` | Stage, progress, attempts, history and warnings |
| GET | `/api/v1/ocr/result/{document_id}` | Complete text, page results and invoice summary |
| GET | `/api/v1/ocr/metrics` | Organization-scoped operational metrics |
| GET | `/api/v1/documents/{document_id}/download-url` | Signed original-document link |
| GET | `/api/v1/documents/{document_id}/file` | Validate signed token and download |
| GET | `/health` | API/database health |

Interactive API documentation is at `/docs`. Results return HTTP 409 until complete. The result JSON uses `schema_version: "1.0"`, `document_id`, `text`, `page_count`, `pages`, `confidence`, `review_required`, `next_stage`, `processing_time`, `warnings`, and `invoice_summary`.

## 11. Running and testing locally

Run from the repository root with the existing `.env`; do not overwrite it blindly.

Terminal 1:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Terminal 2:

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

Open `http://127.0.0.1:8000`. Restart the relevant processes after environment changes; the worker does not automatically reload changed code. Refresh the browser after frontend changes.

Checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app/static/app.js
```

The last complete test run reported **28 passed, 1 skipped**. The skipped test requires native Tesseract. Passing tests include digital PDF extraction, file validation, tenant isolation, signed download checks, job concurrency/idempotency, retries, expired leases, review handling, authentication and field-extraction regressions. Several OCR routing/fallback tests use mocked providers; they do not prove real PaddleOCR recognition works.

## 12. Next work, in order

1. Install PaddlePaddle and PaddleOCR in a compatible environment; verify package imports and the Paddle runtime. At this handoff both packages are absent.
2. Run PaddleOCR on one clear sample invoice image directly. Inspect real text, confidence and coordinates before integrating the entire workflow.
3. Configure `OCR_PROVIDER=paddle` and `PADDLE_LANGUAGE=en`. Decide explicitly whether to install Tesseract as fallback or leave `OCR_FALLBACK` empty during Paddle-only verification.
4. Account for initial model downloads and startup time. Verify subprocess failures and timeouts are diagnosable.
5. Test JPG/PNG upload, a scanned PDF, and a mixed multipage PDF end to end with real OCR. Add native Paddle tests.
6. Evaluate recognition and field accuracy on several fictional or anonymized invoice layouts. Prioritize customer, final amount, invoice number and date. Preserve the column-separation regressions.
7. Start and validate PostgreSQL, Redis and Celery through the existing Docker configuration. The Compose build uses Python 3.11 and enables optional Paddle installation with `INSTALL_PADDLE=true`.
8. Plan how to retain existing SQLite records and local objects if moving them to PostgreSQL/MinIO; switching environment settings alone does not migrate existing data.
9. Add a real AI extraction module consuming the OCR result contract. Currently `AI_EXTRACTION_PENDING` is a stage and result API handoff only; no downstream AI service is invoked.
10. Add accounting validation and human review workflows after extraction is proven.

Do not present the existing regex/geometry summary as an AI extraction implementation. Do not claim the Docker/native OCR integrations are verified until they have actually been run. Start by inspecting the provided files and checking installation status, then guide me through one task at a time.
