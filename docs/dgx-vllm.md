# vLLM su NVIDIA DGX (aarch64)

## Wheel ufficiali

**Non esistono** wheel PyPI `vllm` dedicate alla DGX. Su arm64 (Grace-Hopper GH200, Grace-Blackwell) il pacchetto generico si installa, ma **FlashInfer** compila kernel JIT e richiede l’architettura GPU corretta.

Opzioni consigliate:

| Approccio | Quando |
|-----------|--------|
| **Senserve Docker** (questo repo) + env sotto | Uso normale con compose |
| **Build immagine ufficiale** `vllm/vllm-gh200-openai` | Massima compatibilità NVIDIA |
| **Community** `rajesh550/gh200-vllm` | Immagine prebuild GH200 |

### Build immagine vLLM ufficiale per GH200

```bash
git clone https://github.com/vllm-project/vllm.git
cd vllm
DOCKER_BUILDKIT=1 docker build . \
  --file docker/Dockerfile \
  --target vllm-openai \
  --platform linux/arm64 \
  -t vllm/vllm-gh200-openai:latest \
  --build-arg max_jobs=66 \
  --build-arg nvcc_threads=2 \
  --build-arg torch_cuda_arch_list="9.0 10.0+PTX" \
  --build-arg RUN_WHEEL_CHECK=false
```

Documentazione: [vLLM Docker arm64](https://docs.vllm.ai/en/stable/deployment/docker/).

## Errore `FlashInfer requires GPUs with sm75 or higher`

Spesso **non** significa GPU troppo vecchia: significa che FlashInfer non ha rilevato nessuna arch CUDA nel container (`TARGET_CUDA_ARCHS` vuoto).

### Fix già in `compose.yaml`

- `VLLM_USE_FLASHINFER_SAMPLER=0` — campionamento top-k/p senza JIT FlashInfer
- `FLASHINFER_CUDA_ARCH_LIST=9.0a` — GH200 / Hopper (GPU sm90)
- `TORCH_CUDA_ARCH_LIST=9.0a`
- `attention_backend=FLASH_ATTN` in `config/models.toml`

### DGX Spark (Blackwell, sm12x)

Se la GPU è Blackwell, in `compose.yaml` prova:

```yaml
TORCH_CUDA_ARCH_LIST: "12.0a"
FLASHINFER_CUDA_ARCH_LIST: "12.0a"
```

Richiede CUDA toolkit ≥ 12.9 nel container (immagine `devel` o più recente).

## Verifica GPU nel container

```bash
docker compose run --rm senserve nvidia-smi
docker compose run --rm senserve uv run python -c \
  "import torch; print(torch.cuda.get_device_capability())"
```

Atteso su GH200: `(9, 0)` o simile.

## vLLM Sleep Mode (switch veloce)

Senserve usa sleep mode per switch automatico tra modelli (`SENSERVE_SLEEP_MODE=level2`, `VLLM_SERVER_DEV_MODE=1` in `compose.yaml`).

Verifica che l’immagine esponga gli endpoint admin prima del deploy:

```bash
uv run python scripts/verify_vllm_sleep.py --skip-serve  # con vLLM già avviato su --port
# oppure avvio completo (richiede GPU):
# VLLM_SERVER_DEV_MODE=1 vllm serve Qwen/Qwen3.5-0.8B --enable-sleep-mode --port 8010
```

Il **primo** caricamento di ogni modello resta lento (HF + warm-up); dal **secondo** switch in poi il wake è molto più rapido del restart completo.
