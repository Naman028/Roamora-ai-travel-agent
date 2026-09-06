# Roamora Base/V1 evaluation

This package implements AI Stage 1 only. It does not change model, prompt,
generation, provider, budget, public API, BFF, or frontend behavior.

## Lanes

- `model`: calls the existing prompt builder, loader, tokenizer template, and
  generation function exactly once. It does not invoke providers, retry, or
  application fallback.
- `application`: calls the existing `run_travel_generation` path. Evaluation
  doubles replace provider clients, record provider calls, and prevent live
  network traffic. Existing application retry and fallback behavior remains
  active and is measured.

The raw final suite is a private holdout and is not stored in the repository.
`suites/v1/final.metadata.json` records only its version, review status,
case/turn counts, logical seal path, and SHA-256. `suites/v1/seal.json` retains
the original logical path and byte hash, so moving the file does not change the
seal. Any content change requires an explicit new suite version and seal.

Both v1 suites are marked `pending_human_review`. They are sealed draft golden
cases, not promotion evidence, until domain review is complete. Non-English
cases additionally require review by a fluent speaker. Review changes require
a new explicit suite version rather than silently changing v1.

## Isolated environment

Use Python 3.13.2. Do not install the ML stack into `backend/.venv`, which is
the canonical contract-test environment.

```powershell
py -3.13 -m venv backend/.venv-evaluation
backend/.venv-evaluation/Scripts/python.exe -m pip install --no-deps `
  torch==2.13.0+cu126 `
  --index-url https://download.pytorch.org/whl/cu126
backend/.venv-evaluation/Scripts/python.exe -m pip install `
  -r backend/requirements-evaluation.txt `
  --extra-index-url https://download.pytorch.org/whl/cu126
backend/.venv-evaluation/Scripts/python.exe -m pip check
```

Required evaluator packages are pinned in `requirements-evaluation.txt` and
the referenced runtime requirements. Every report records the installed
versions of Torch, Transformers, Accelerate, PEFT, huggingface-hub,
Safetensors, and Pydantic.

## Validate and run

Routine validation uses only tracked development inputs and holdout metadata;
it neither locates nor opens the private final cases:

```powershell
backend/.venv/Scripts/python.exe -m backend.evaluation validate
```

An explicitly authorized final validation or run must provide the raw holdout
through `ROAMORA_FINAL_EVALUATION_SUITE_PATH`. The resolved file must be outside
the repository and must match the sealed SHA-256, schema, metadata, fixture
references, and scenario-family isolation before model loading begins.

```powershell
$env:ROAMORA_FINAL_EVALUATION_SUITE_PATH = 'C:\protected\roamora\v1\final.json'
backend/.venv/Scripts/python.exe -m backend.evaluation validate --include-final
```

Do not put this path in a tracked environment file, dataset-builder input, or
training configuration. Clear the process environment variable after the
authorized evaluation session.

Model identity and generation require the isolated ML environment and locally
cached artifacts. Importing `backend.evaluation` forces `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` before any optional Hugging Face, Transformers, or
PEFT import; the CLI reasserts those values rather than honoring inherited
online values. Hugging Face Hub 1.13.0 reads both at import time, and
Transformers 5.10.1 converts offline mode to `local_files_only=True` for model,
tokenizer, configuration, and remote-code resolution. Identity resolution uses
only explicit local paths or existing cache/checkpoint entries and never calls
a Hub download API, and identity capture itself runs inside the socket guard.
If Hub constants were already imported in online mode by an embedding process,
the evaluator fails instead of pretending a later environment change is
effective. Missing artifacts fail before generation. Per-turn socket guards
remain a separate fail-closed layer.

```powershell
backend/.venv-evaluation/Scripts/python.exe -m backend.evaluation identity
backend/.venv-evaluation/Scripts/python.exe -m backend.evaluation run `
  --split development `
  --models base fine_tuned `
  --lanes model application
```

Use `--case-limit N` only for harness smoke tests. A promotion comparison must
use the complete sealed split. Base and V1 execute sequentially. Reports and
raw outputs are written under the already ignored
`backend/fine_tuning/outputs/evaluation/` directory. Raw outputs are local
evaluation artifacts and must not be committed or copied into routine logs.
The final split must not be run for routine development, prompt adjustment,
dataset construction, or scorer tuning.

## Measurement boundaries

| Component | Measures | Does not establish | Main false-positive risk | Main false-negative risk |
|---|---|---|---|---|
| Model lane | One raw generation with the production prompt/tokenizer/settings | Public application behavior or provider quality | Keyword expectations can reject valid paraphrases | Key-based claim checks can miss novel claim shapes |
| Application lane | Current orchestration, retries, fallbacks, normalization, and public response | Quality of live providers | Controlled failures may be harsher than a real provider response | Newly added provider entry points need corresponding doubles |
| Network guard | Attempts to use outbound socket connection APIs during a turn | Network isolation outside the evaluator process | A legitimate local-socket dependency would terminate the run | A non-socket or child-process transport is outside this guard |
| Strict scorer | Exact JSON and contract conformance | General answer usefulness | Exact structure rejects semantically useful non-contract output | Best-effort evidence extraction may miss malformed claims |
| Grounding scorer | Unknown IDs and selected factual fields against fixtures | Complete factual correctness | Generic duration text can resemble a route claim | Unrecognized fields or paraphrased claims can escape detection |
| Language markers | Presence of small diagnostic marker sets | Fluency, correctness, or language support | Borrowed marker words can pass | Good output can omit every configured marker |
| Timings | Synchronous boundaries exposed by the current runtime | True TTFT or isolated Lane B normalization time | Cold/cache state can differ from production | Background work outside the measured boundary is omitted |
| Multi-turn cases | Current behavior for sequential turns sharing a session ID | Durable conversational memory | Explicit fields in the follow-up can make continuity look better | The current application does not consume session history |
| Failure ownership | Observable signal grouping by evaluator lane | Causal model, dataset, or provider attribution | One defect can emit multiple signals | Unmodeled defects have no ownership signal |

The provider doubles and monkeypatch-based instrumentation require sequential
execution in one process. Parallel evaluator turns are unsupported. All loops
are bounded by the selected models, lanes, cases, and at most four turns per
case; Lane A has exactly one generation call and the unchanged application path
has at most two.

True time to first token is unavailable because the protected runtime is
non-streaming. Fine-tuned load time also combines backbone and adapter loading.
Both fields remain explicitly null rather than being estimated.

If scorer or report logic is corrected after a completed generation run, use
`rescore` rather than regenerating. It verifies the sealed suite and protected
model identities, copies the persisted raw evidence into a new local run, and
records both generation-time and scoring-time evaluator versions.
The source report's `run_id` is retained only as metadata. A rescore uses a new
bounded evaluator-generated ID for its canonicalized output directory and
fails unless that directory resolves beneath the requested output root.

```powershell
backend/.venv-evaluation/Scripts/python.exe -m backend.evaluation rescore `
  --report backend/fine_tuning/outputs/evaluation/RUN_ID/report.json
```
