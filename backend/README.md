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
|-- .python-version              # Supported Python patch version
|-- constraints-runtime.txt      # Security-fixed runtime transitive constraint
|-- requirements.txt             # Exactly pinned runtime/model dependencies
|-- requirements-ci.txt          # Exactly pinned contract-test dependencies
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

## Runtime health

- `GET /health` is a liveness check and never invokes a provider or model.
- `GET /ready` checks lifecycle and configuration state without generation. It returns HTTP 503 with `{"status":"not_ready"}` when startup has not completed, preload configuration is invalid, or production mode has no backend API key.
- `GET /api/health` is a deprecated compatibility alias.

Lazy-load readiness does not prove that the first model load, generation, or external provider request will succeed. The service uses one Uvicorn worker per loaded model process by default; request cancellation or timeout does not prove that already-running synchronous GPU work stopped.

## Dependencies

Use Python 3.13.2. `requirements.txt` contains exactly pinned direct production/model dependencies. `requirements-ci.txt` contains the smaller exactly pinned test contract, including Pytest, and deliberately omits the language-model stack. `constraints-runtime.txt` pins the security-required `setuptools==83.0.0` Torch transitive. The Windows and WSL launchers first install only the `torch==2.13.0+cu126` wheel from the official PyTorch CUDA 12.6 index, then resolve its dependencies together with `requirements.txt` from PyPI. The plain `torch==2.13.0` runtime pin accepts that local CUDA build while remaining auditable on provider-neutral CI. Recreate an existing virtual environment after dependency changes, then run `python -m pip check`.

## Test

From the repository root:

```bash
python -m pip install -r backend/requirements-ci.txt
python -m pip check
python -m pytest backend/tests -q -W error
python -m compileall -q backend/app backend/fine_tuning/scripts
```

The test suite covers the API contract, security, request controls, service mapping, budget normalization, adapter parsing, JSON guardrails, logging safety, dependency separation, and liveness/readiness behavior.

## Model assets

Local training data, checkpoints, model weights, and evaluation output are preserved under `fine_tuning/` but ignored by Git because they are generated or too large for normal source control. Store production model artifacts in a model registry or artifact store and inject their locations through environment variables.
