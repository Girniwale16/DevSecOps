# Data Cosmos

Data Cosmos is a Python-based synthetic data studio with a FastAPI backend and a NiceGUI frontend. The application supports three project setup paths:

- CSV ingestion
- DDL parsing
- Schema-first table design

From those inputs, the system profiles data, stores project metadata in DuckDB, supports semantic and relationship inference, offers admin-managed authentication, and generates downloadable synthetic datasets.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `backend/app/main.py` | FastAPI application, upload APIs, project APIs, generation orchestration, time-series APIs |
| `backend/app/auth.py` | Authentication, user management, audit logging, request activity logging |
| `backend/app/engine/` | Parsing, profiling, inference, planning, generation, summary, and time-series helpers |
| `frontend/main.py` | NiceGUI single-page workflow UI and admin screens |
| `frontend/auth.py` | Frontend auth state helpers |
| `docker-compose.yml` | Local two-service deployment for frontend and backend |
| `backend/Dockerfile` | Backend container image |
| `frontend/Dockerfile` | Frontend container image |
| `tests/` | Sample inputs and ad hoc parser/debug scripts |

## Runtime Architecture

- Backend: FastAPI on port `8000`
- Frontend: NiceGUI on port `8181`
- Metadata store: DuckDB file at `backend/data/studio_metadata.db`
- Generated artifacts: `backend/data/uploads` and `backend/data/exports`
- Auth model: bearer-token sessions stored in backend process memory, user and audit state persisted in DuckDB

## Main Capabilities

- Upload one or more CSV files and profile them
- Upload DDL and normalize schema metadata
- Define schema tables manually in the frontend
- Infer semantic column types
- Detect likely PII columns
- Infer table relationships
- Expand categorical values
- Produce project summaries and assistant responses with optional Groq-backed LLM calls
- Plan multi-table row propagation for synthetic generation
- Generate CSV, Parquet, and ZIP outputs
- Analyze and generate time-series datasets
- Admin user management with audit and activity logs

## Local Development

### Option 1: Batch scripts

Backend:

```bat
run_backend.bat
```

Frontend:

```bat
run_frontend.bat
```

### Option 2: Docker Compose

```powershell
docker compose up --build
```

## Key Environment Variables

### Backend

| Variable | Default |
| --- | --- |
| `AUTH_USERNAME` | `admin` |
| `SUPER_ADMIN_USERNAME` | `superadmin` |
| `GROQ_API_URL` | `https://api.groq.com/openai/v1/chat/completions` |
| `GROQ_MODEL` | `llama-3.1-8b-instant` |
| `GROQ_SUMMARY_MODEL` | `llama-3.1-8b-instant` |
| `GROQ_REL_MODEL` | `llama-3.1-8b-instant` |
| `GROQ_ASSISTANT_MODEL` | `llama-3.1-8b-instant` |
| `MAX_CSV_UPLOAD_MB` | `50` |
| `MAX_DDL_UPLOAD_MB` | `50` |
| `LARGE_UPLOAD_THRESHOLD_MB` | `25` |
| `FAST_PROFILE_SAMPLE_ROWS` | `10000` |
| `CSV_PROFILE_SAMPLE_FOR_LARGE_FILES` | `false` |

### Frontend

| Variable | Default |
| --- | --- |
| `BACKEND_URL` | `http://localhost:8000` |
| `UI_TIMEZONE` | `Asia/Kolkata` |
| `UPLOAD_REQUEST_TIMEOUT` | `300` |
| `MAX_CSV_UPLOAD_MB` | `50` |
| `MAX_DDL_UPLOAD_MB` | `50` |
| `NICEGUI_HOST` | `0.0.0.0` |
| `NICEGUI_PORT` | `8181` |

## Documentation Pack

Full repository documentation is available in `docs/repository-documentation.md`. It contains:

- `brd.md`
- `prd.md`
- `page_flow.md`
- `functional_spec.md`
- `technical_doc.md`

## Notes

- `README.md` was empty before this update.
- `tests/` currently contains sample data and script-style checks rather than a formal automated test suite.
- `backend/app/main.py.authbak` and `frontend/main.py.authbak` appear to be backup copies and are not part of the container startup path.
