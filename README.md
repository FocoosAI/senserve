# Senserve

OpenAI-compatible multimodal gateway over **vLLM** (no Ray). One active model per GPU; fast hot-swap via [vLLM Sleep Mode](https://vllm.ai/blog/2025-10-26-sleep-mode) (lazy pool, one `vllm serve` per catalog model).

## Ports

| Service | Port | Env |
|---------|------|-----|
| Senserve API | **8787** | `SENSERVE_API_PORT` |
| Open WebUI | **8788** | (Docker maps `8788→8080`) |
| vLLM workers (internal) | **8000+** | `SENSERVE_WORKER_BASE_PORT` (+1 per enabled model) |

## Quick start

```bash
uv sync --extra dev
uv run senserve                    # loads default: Qwen3.5 0.8B
uv run senserve --no-load          # API only, no model in VRAM
uv run senserve --load gemma-4-26b-a4b-it
```

Config: `config/models.yaml` (optional `config/models.local.yaml`). Edit via dashboard **Configuration** section or API `GET/PUT /v1/admin/config`. Startup loads a model only when one entry has `default: true` (or use `--load` / `--no-load`).

## Dashboard

Operational UI (model status, worker pool, load/switch, catalog editor): **http://localhost:8787/ui/**

Bind Senserve to localhost only in untrusted networks; admin endpoints (including config save) have no auth.

## API

- `GET /health`
- `GET /v1/models` — catalog; `loaded`, `capabilities`, per-model `status`
- `POST /v1/chat/completions`
- `POST /v1/admin/models/load` — `{"model_id": "..."}`
- `GET /v1/admin/models/status`
- `GET /v1/admin/config` — catalog YAML as JSON (base file)
- `PUT /v1/admin/config` — save catalog (blocked while switching or if active model would change)
- `GET /v1/admin/vllm/flags` — vLLM `serve` CLI flags for UI autocomplete

During model switch: **503** + `Retry-After` (default 30s, `SENSERVE_SWITCH_RETRY_AFTER_S`).

Chat bodies are forwarded to vLLM as-is by default (`SENSERVE_INLINE_REMOTE_MEDIA=0`). Optional `SENSERVE_INLINE_REMOTE_MEDIA=1` fetches HTTP(S) `video_url` into base64 data URLs. Set `allowed_local_media_path` in `config/models.yaml` and mount media into the container (see compose `./datasets:/datasets`).

Model switching from Open WebUI is automatic: Senserve sleeps the active vLLM worker and wakes the target (no manual `/sleep` calls). Set `SENSERVE_SLEEP_MODE=off` to fall back to kill+restart.

## Docker + Open WebUI

```bash
docker compose up --build
```

- **Open WebUI:** http://localhost:8788 (chat UI; model picker uses Senserve `GET /v1/models`)
- **Senserve API:** http://localhost:8787/v1

Open WebUI is preconfigured with `OPENAI_API_BASE_URL=http://senserve:8787/v1`. Senserve starts with **Qwen3.5 0.8B** loaded. To switch models, pick another id in the UI (e.g. `qwen3-vl-4b-awq`, `gemma-4-26b-a4b-it`); Senserve hot-swaps on the first chat request. First start can take several minutes (HF download + vLLM warmup).

Requires NVIDIA GPU and the NVIDIA Container Toolkit.

**DGX / GH200 (aarch64):** see [docs/dgx-vllm.md](docs/dgx-vllm.md) if you hit FlashInfer or wheel issues.

## Dataset video inference

With Docker running and a video-capable model loaded (e.g. `qwen3-vl-4b-awq`):

```bash
uv run python scripts/infer_dataset_videos.py --dataset datasets --model auto
```

Encodes each video as a base64 data URL by default. Use `--media file` for `file:///datasets/<name>.mp4` when the container has `./datasets` mounted.

## Tests

```bash
uv run pytest tests/ -q
```
