# Roamora Backend

This folder contains the complete FastAPI service and its model/fine-tuning support code.

## Structure

```text
backend/
|-- app/                         # FastAPI application package
|   |-- core/                    # Configuration, security, and request controls
|   |-- models/                  # Pydantic request/response contracts
|   |-- routes/                  # HTTP endpoints
|   `-- services/                # Model and travel API services
|-- fine_tuning/
|   |-- scripts/                 # Adapter loader and JSON guardrail
|   |-- data/                    # Local training data
|   |-- checkpoints/             # Local adapter checkpoints
|   `-- outputs/                 # Evaluation and training output
|-- tests/                       # Backend pytest suite
|-- requirements.txt
|-- start_backend_windows.ps1
`-- start_backend_wsl.sh
```

## Configure

Copy `.env.example` to `.env`. At minimum, configure the model source and production HTTP security values appropriate for the deployment.

Important settings include:

- `HF_TOKEN`, `BASE_MODEL_ID`, and adapter source settings
- `BACKEND_PRELOAD_MODEL` (`fine_tuned`, `base`, or `none`)
- `BACKEND_ENV` and `BACKEND_API_KEY`
- `BACKEND_CORS_ORIGINS`
- `BACKEND_GENERATION_TIMEOUT_SECONDS`
- `BACKEND_RATE_LIMIT_PER_MINUTE`
- `BACKEND_MAX_CONCURRENT_GENERATIONS_PER_CLIENT`
- `MODEL_MAX_INPUT_TOKENS`, `MODEL_MAX_NEW_TOKENS`, and `MODEL_TEMPERATURE`

Do not commit or package `.env`.

## Run

From the repository root:

```powershell
.\backend\start_backend_windows.ps1
```

or:

```bash
./backend/start_backend_wsl.sh
```

Manual startup from the repository root:

```bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 9000 --workers 1
```

Use `BACKEND_PRELOAD_MODEL=none` for contract testing without loading the model at startup.

## Test

From the repository root:

```bash
python -m pytest backend/tests -q
python -m compileall backend/app backend/fine_tuning
```

The test suite covers the API contract, security, request controls, service mapping, budget normalization, adapter parsing, and JSON guardrail behavior.

## Model assets

Local training data, checkpoints, model weights, and evaluation output are preserved under `fine_tuning/` but ignored by Git because they are generated or too large for normal source control. Store production model artifacts in a model registry or artifact store and inject their locations through environment variables.
