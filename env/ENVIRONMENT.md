# Environment (recorded 2026-10-06)

| Item | Value |
| --- | --- |
| Host | GB10, aarch64, Ubuntu 24.04.1, 20 CPU cores |
| Python | 3.12.3, venv with `--system-site-packages` |
| From the host | torch 2.7.0 (NVIDIA build), transformers 5.14.1, Pillow 11.1 |
| Pinned in the venv | numpy 1.26.4 (must match the host build of torch/scipy), psycopg 3, FastAPI, PyJWT, rapidocr-onnxruntime 1.4.4 (onnxruntime 1.30, opencv 4.11) |
| Database | PostgreSQL 16.15 + pgvector 0.6.0 (Ubuntu arm64 packages, unpacked in user space by `scripts/setup_postgres.sh`) |
| Embedding model | BAAI/bge-base-en-v1.5 from the local Hugging Face cache, CPU inference |

Note: installing `rapidocr-onnxruntime` without the numpy pin pulled numpy 2.x into the venv, which
broke the host's scipy/torch (`numpy.core.multiarray failed to import`). The pin fixes it.
