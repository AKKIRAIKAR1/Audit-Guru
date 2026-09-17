# LedgerLens — Invoice OCR Processing

A runnable OCR module for the AI-Powered Accounts Payable System. FastAPI serves the API and invoice workspace; workers validate stored invoices, extract digital PDF text or run image OCR, and persist page text, geometry, quality indicators and table regions. AI field extraction and accounting validation are downstream modules, not simulated here.

## Start with Docker (recommended)

Requires Docker Compose and a running Docker engine.

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

Open **http://localhost:8000** and sign in with name **Admin** and your configured password. Swagger is at **http://localhost:8000/docs**. Upload a PDF, JPG or PNG; background processing starts automatically. No sample invoices or fabricated metrics are loaded.

Compose starts PostgreSQL, Redis, private MinIO, the API, Celery workers and the job dispatcher. Tesseract English OCR is installed in the application container. Objects, jobs and results survive container restarts. `docker compose down` stops services without deleting volumes.

### PaddleOCR with Tesseract fallback

The default is Tesseract for a smaller installation without model downloads. To use the recommended PaddleOCR provider, add these values to `.env`, then rebuild:

```dotenv
INSTALL_PADDLE=true
OCR_PROVIDER=paddle
OCR_FALLBACK=tesseract
PADDLE_LANGUAGE=en
```

Paddle downloads models on initial use and needs substantially more memory and startup time. For offline deployments, provision its model cache in the worker image. Raise `OCR_TIMEOUT_SECONDS` for initial model loading; keep the combined provider timeout below `JOB_LEASE_SECONDS`. Paddle results use **line** granularity; Tesseract and direct PDF extraction use **word** granularity. Both include the `granularity` field.

## Local development without Docker

Python 3.11+; Tesseract must be installed and on PATH for scanned files. Digital PDFs work without it. SQLite and private local object files replace PostgreSQL/MinIO locally. The polling worker uses the same durable jobs and processing code as Celery.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal in the same folder:

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

Keep both processes running. The local worker processes documents sequentially. Use the Docker Celery stack for concurrent processing. Never put the private `data/` directory under a public web server.

## API

The browser signs in through `/api/v1/auth/login` and receives an HttpOnly, SameSite session cookie. Sessions expire after eight hours; `/api/v1/auth/logout` revokes them on the server. The administrator accesses `ADMIN_ORGANIZATION`, preserving the existing demo invoices. Passwords are checked against `ADMIN_PASSWORD_HASH`, a salted PBKDF2 hash. Optional machine-to-machine `X-API-Key` credentials remain configurable through `API_KEYS`, which is empty by default; callers cannot choose another organization in a request. Unknown and foreign document IDs return the same 404 response.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/v1/documents` | Multipart `file` upload; validates and queues atomically |
| GET | `/api/v1/documents?limit=50&offset=0` | Tenant document list |
| POST | `/api/v1/ocr/process` | Idempotent start or explicit retry of a failed job |
| GET | `/api/v1/ocr/status/{document_id}` | Stage, page progress, attempts, errors and history |
| GET | `/api/v1/ocr/result/{document_id}` | Versioned complete text and per-page layout JSON |
| GET | `/api/v1/ocr/metrics` | Tenant success rate, queue, failures, confidence and timing |
| GET | `/api/v1/documents/{document_id}/download-url` | Five-minute signed original-document URL |
| GET | `/health` | API/database liveness |

Use the invoice workspace to upload files after signing in. For a custom API client, call `POST /api/v1/auth/login` with JSON `username` and `password`, retain the response cookie, and send `X-Requested-With: LedgerLens` on login/logout and authenticated write requests.

The upload response contains `document_id` and `job_id`. Start requests use `{"document_id":"..."}`. Results return 409 while processing. Content-hash deduplication is scoped to each organization. Repeated uploads return the existing document and job. Failed documents expose a retry action; completed jobs are immutable through the start API.

## Processing and layout contract

- Validate extension, actual file signature, decoded image, size, pixel count, PDF integrity, password protection and page count; revalidate and verify SHA-256 in the worker.
- Route each page independently. Text-bearing PDF pages use PyMuPDF. Pages with no useful text or substantial raster content use OCR, including mixed PDFs with digital headers over scans.
- Raster pages use EXIF orientation, bounded rendering, grayscale and contrast enhancement. Laplacian sharpness and OCR confidence produce review warnings. These are heuristics, not calibrated accuracy scores.
- Digital text bounding boxes use unrotated PDF coordinates in points; OCR boxes use pixels of the oriented/resized image whose dimensions are in the response. Every page explicitly describes its coordinate reference.
- Digital PDF tables include extracted rows; scanned images include heuristic ruled-table regions and references to enclosed words. Borderless scanned-table structure, merged cells and semantic field recognition require a later document-understanding integration.
- Digital extraction confidence is `null`, since it is not an OCR prediction. Document confidence averages recognized OCR word/line scores only. A blank OCR page has page confidence 0 and always requires review.
- Results become visible atomically only after all pages finish. Page progress is persisted during work. Low-quality or blank content reaches `HUMAN_REVIEW`; clean results reach `AI_EXTRACTION_PENDING`. The AI extraction module can poll that stage and consume the result API.

Normal history: `UPLOADED → VALIDATING → OCR_PROCESSING → OCR_COMPLETED → AI_EXTRACTION_PENDING`. Quality problems end in `HUMAN_REVIEW`; transient failures use `OCR_RETRY_PENDING`; exhausted/permanent failures use `OCR_FAILED`.

## Reliability and storage

`documents`, `ocr_jobs`, and `ocr_results` are SQLAlchemy tables. The initial schema is created at API startup. Introduce versioned migrations before evolving an existing production database.

The SQL job record is also the durable dispatch outbox: a broker outage cannot lose an accepted upload. Celery beat scans due jobs every five seconds. Duplicate task deliveries are guarded by an atomic SQL claim. Each worker has a unique run token and a renewable lease; expired leases can be reclaimed. Transient failures retry with exponential backoff, up to `MAX_ATTEMPTS`. Stale workers cannot commit results after another worker takes ownership. OCR providers run in child processes with timeouts; failed primary providers fall back. Celery also has whole-task soft/hard limits. Long documents that exceed those limits eventually become failed jobs for operator inspection.

Store API and worker processes on the same local filesystem only in local-storage mode. Use S3 mode for distributed workers. MinIO objects are private and downloaded through short-lived, tenant-bound HMAC URLs. AWS S3 can use `S3_SERVER_SIDE_ENCRYPTION=AES256` or `aws:kms`; local/MinIO at-rest encryption requires encrypted volumes or a configured MinIO KMS. Encryption is not silently claimed for an unencrypted development volume.

Optional malware scanning uses ClamAV INSTREAM: set `CLAMAV_HOST` and `CLAMAV_PORT` to a running scanner. When configured, uploads fail closed if scanning is unavailable or rejects content. The default development stack does not run a scanner.

Before internet exposure, configure administrator credentials and the signing secret, configure TLS, secret management, request-body limits/rate limits at the gateway, encrypted storage/backups and malware scanning. Administrator provisioning is configuration-based; SSO, key administration and human review assignment are outside this module. `/health` checks the database only; monitor Celery worker health and queue age separately. Metrics are tenant-scoped operational summaries, not a time-series monitoring system.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app/static/app.js
```

Tests use isolated temporary SQLite/storage. They exercise real multi-page PDF extraction, validation failures, tenant access, signed downloads, duplicate/concurrent jobs, mixed-page routing, review flags, provider fallback, retry exhaustion and recovery. Provider routing tests substitute a controlled OCR provider, so they do not require native OCR models. Run the optional native integration test with Tesseract installed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_native_ocr.py
```

Implementation references: [PyMuPDF text extraction](https://pymupdf.readthedocs.io/en/latest/app1.html), [PyMuPDF table extraction](https://pymupdf.readthedocs.io/en/latest/page.html), and [PaddleOCR 3.x inference API](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/OCR.html).

## Invoice grid

The workspace includes a paginated invoice grid with page-local search, completion/review filters, and a View action to open the existing text and layout details. Completed documents include invoice number/date, labeled vendor/customer/contact details, total, tax and balance when detected. These are conservative, unverified display hints derived from labeled OCR text and word coordinates. Labels are paired with values in the same row or aligned immediately below, keeping separate invoice columns apart. Final-total labels take precedence over generic totals; dates exclude time/contact text and invoice identifiers exclude adjacent customer names; missing or conflicting values appear as a dash. Payment status is not inferred from OCR. Existing completed documents populate automatically without reprocessing or a database migration.


## Administrator login

The sign-in page uses Name and Password. Set `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, and `ADMIN_ORGANIZATION` in `.env`. The requested initial account is configured locally; no password is embedded in browser assets. Changing the username, password hash or organization invalidates existing sessions. Set `SESSION_COOKIE_SECURE=true` when serving over HTTPS; leave false for local HTTP. Restart FastAPI after configuration changes. Existing invoices remain under `demo-organization`. API keys are disabled by default.
#   A u d i t - G u r u  
 