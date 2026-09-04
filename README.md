# Roamora AI Travel Assistant

[![CI](https://github.com/Naman028/Roamora-ai-travel-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Naman028/Roamora-ai-travel-agent/actions/workflows/ci.yml)

Roamora is a full-stack AI travel-planning application built with Next.js 16 and FastAPI. It combines live travel-service context with base or fine-tuned language-model generation, validates the generated data, and presents the result through a responsive travel dashboard.

## Features

- Responsive desktop, tablet, and mobile workspace with chat, current-trip, and saved-trip views.
- Server-only Next.js API route that validates browser input and securely forwards requests to FastAPI.
- Accessible per-request Base/Fine-tuned selector with immutable request snapshots, original-model retry, and BFF response verification.
- Weather, flight, hotel, event, itinerary, and budget service integration.
- Structured dashboard responses for trip summaries, itineraries, maps, recommendations, weather, events, and budget details.
- JSON extraction, normalization, schema validation, and safe fallback behavior for model output.
- Clear separation between verified live prices and model-generated estimates.
- Consistent API error envelopes with request IDs and safe client-facing messages.
- API-key authentication, explicit CORS configuration, request timeouts, rate limits, and per-client concurrency limits.
- Response-size limits, `no-store` behavior, literal rendering of assistant text, and production security headers.
- Automated unit, contract, browser, responsive, accessibility, and dependency-security checks.

## Architecture

```text
Browser
  |
  | POST /api/chat with explicit model_variant
  v
Next.js server-only route
  |
  | validated request + X-API-Key
  v
FastAPI /chat
  |-- live travel-service context
  |-- base or fine-tuned model generation
  |-- JSON guardrails and budget normalization
  v
Validated dashboard response with selected_model equality verification
```

```text
Roamora-ai-travel-agent/
|-- frontend/
|   |-- app/                  # Next.js App Router and server route handlers
|   |-- components/           # Responsive application and UI components
|   |-- features/             # Chat contracts, state, hooks, and API calls
|   |-- lib/                  # Domain, environment, and error utilities
|   |-- server/               # Server-only FastAPI client
|   |-- __tests__/            # Vitest unit and integration tests
|   `-- e2e/                  # Playwright and Axe browser tests
|-- backend/
|   |-- app/                  # FastAPI routes, models, controls, and services
|   |-- fine_tuning/scripts/  # Adapter loading and JSON guardrails
|   `-- tests/                # Pytest contract and service tests
|-- .github/workflows/ci.yml  # Four-job GitHub Actions pipeline
|-- .gitignore
`-- AGENTS.md
```

Secrets, local environments, dependencies, build output, test reports, caches, archives, training data, and model checkpoints are excluded from Git.

## Technology

- Next.js 16.3.4, React 19, TypeScript, and TanStack Query
- FastAPI, Pydantic, Python 3.13.2, and HTTPX
- Hugging Face Transformers, PEFT/LoRA, PyTorch, and Safetensors
- Vitest, Testing Library, Pytest, Playwright, and Axe
- GitHub Actions, npm audit, and pip-audit

## Local setup

### Backend

Copy the environment example and provide the credentials required by the services and model configuration you intend to use:

```powershell
Copy-Item backend\.env.example backend\.env
```

Start the backend on Windows:

```powershell
.\backend\start_backend_windows.ps1
```

Or start it from WSL/Linux:

```bash
./backend/start_backend_wsl.sh
```

The launchers create a backend virtual environment, install the exactly pinned direct runtime dependencies in `backend/requirements.txt`, and start `backend.app.main:app` on `http://127.0.0.1:9000` by default. Recreate the virtual environment after dependency-file changes. Set `BACKEND_PORT` to use another port.

The backend records Python `3.13.2` in `backend/.python-version`. Production/model dependencies live in `backend/requirements.txt`; contract-test dependencies live separately in `backend/requirements-ci.txt` so Pytest is not installed as part of the production runtime. `backend/constraints-runtime.txt` fixes the one security-required Torch transitive. The platform launchers install the official CUDA 12.6 build of the pinned Torch release before installing the remaining runtime requirements, preserving the NVIDIA GPU runtime instead of silently selecting a CPU-only wheel.

### Frontend

From the repository root:

```powershell
Copy-Item frontend\.env.example frontend\.env.local
Set-Location frontend
npm ci
npm run dev
```

Ensure `BACKEND_API_URL` points to the FastAPI service and `BACKEND_API_KEY` matches the backend configuration. Open `http://localhost:3000`.

## Validation

Run frontend quality checks from `frontend/`:

```bash
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Run the production browser suite after building:

```bash
npx playwright install chromium
npm run test:e2e
```

Run deterministic backend checks from the repository root without loading the model or calling external providers:

```bash
python -m pip install -r backend/requirements-ci.txt
python -m pip check
python -m pytest backend/tests -q -W error
python -m compileall -q backend/app backend/fine_tuning/scripts
```

The current suites contain 67 frontend tests, 54 backend tests, and 18 Playwright tests covering model selection, browser integration, responsive layouts, accessibility, output safety, runtime health, logging safety, dependency policy, and production security headers.

## Continuous integration

GitHub Actions runs on pushes and pull requests targeting `main`. The pipeline contains four independent jobs:

- **Frontend quality:** clean install, lint, type-check, unit tests, and production build.
- **Backend quality:** isolated CI dependency install, dependency consistency, Pytest with warnings treated as errors, and Python compilation.
- **Browser E2E:** Chromium installation, production build, responsive behavior, integration tests, and Axe checks.
- **Dependency security:** npm audit plus separate pip-audit checks for runtime and CI dependency sets.

CI uses mocked or contract-level integrations. It does not require repository secrets, call paid providers, or download the production language model.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Process liveness; no provider or model calls |
| `GET` | `/ready` | Runtime/configuration readiness; no generation call |
| `GET` | `/models` | Available model variants |
| `POST` | `/chat` | Generate a validated travel response |
| `POST` | `/api/model/test` | Exercise the configured model through the protected contract |
| `GET` | `/api/weather/{city}` | Retrieve current weather context |
| `POST` | `/api/flights/search` | Search configured flight data |
| `POST` | `/api/hotels/search` | Search configured hotel data |
| `GET` | `/api/events/{city}` | Retrieve local event context |
| `POST` | `/api/itinerary/generate` | Generate an itinerary from service context |

Protected backend endpoints require the `X-API-Key` header. The browser communicates through the same-origin frontend `POST /api/chat` route, so the backend URL and API key remain server-only. The deprecated `/api/health` endpoint remains only as a compatibility alias; new operational checks should use `/health` and `/ready`.

`/ready` returns `503 {"status":"not_ready"}` until application startup completes, when `BACKEND_PRELOAD_MODEL` is invalid, or when production mode lacks a backend API key. With lazy loading (`BACKEND_PRELOAD_MODEL=none`), readiness confirms runtime configuration, not that a first model generation or external provider call will succeed.

## Production notes

No deployment topology is selected. Validate the frontend with `npm run build` and run the local production server with `npm run start`; add provider-specific packaging only in a separately approved deployment phase.

Run one Uvicorn worker per loaded model process unless memory profiling confirms that more workers fit. Use `BACKEND_ENV=production`, a strong `BACKEND_API_KEY`, explicit `BACKEND_CORS_ORIGINS`, and deployment-managed secrets.

Model weights, adapters, local training data, checkpoints, and generated evaluation output stay outside normal Git history. Configure durable model artifacts through environment variables and store them in an appropriate model registry or artifact store.
