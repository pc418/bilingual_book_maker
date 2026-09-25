# Docker

Use Docker if you do not want to set up Python yourself. The container runs `python make_book.py`, so every flag on this site works unchanged.

## Images

There are two images, built from one Dockerfile on `python:3.12-slim`.

| image | what is in it | how you get it | use it for |
|---|---|---|---|
| `ghcr.io/yihong0618/bilingual_book_maker:latest` | the translator and its Python packages; a few hundred megabytes | `docker pull`; published for `linux/amd64` and `linux/arm64` on every merge to `main`, with `<version>` and `sha-<commit>` tags for releases and single builds | EPUB, TXT, Markdown, SRT, and PDFs on the older text route |
| `bbook_maker:pdf` | the same plus Pandoc 3.11 and the PDF packages (docling and PyTorch; on amd64 the CUDA build, several gigabytes) | **built on your machine** from the Dockerfile's `pdf` target; it is not published | `--to-epub` |

```bash
docker pull ghcr.io/yihong0618/bilingual_book_maker:latest
```

If you want the PDF route, build its image from a clone of the repository:

```bash
docker build --target pdf -t bbook_maker:pdf .
```

## Mounts

- **Your book folder at `/book`.** Pass the book as `/book/<file>`. The translated book is written back into the same folder.
- **A named volume at `/root/.cache`** (the `pdf` image). The docling models download there on the first `--to-epub` run, about 500 MB. Without the volume every run downloads them again.

The container runs as root, so writing into the mounted folder always works. On Linux the files it writes belong to root: `chown` them afterwards, or add `--user $(id -u)`.

Pass the key as an environment variable rather than on the command line: `-e OPENAI_API_KEY`.

## An EPUB, per system

=== "Linux / macOS"

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:latest \
      --book_name /book/my_book.epub \
      --language zh-hans \
      --use_context session
    ```

=== "Windows PowerShell"

    ```powershell
    docker run --rm `
      -v "${PWD}:/book" `
      -e OPENAI_API_KEY `
      ghcr.io/yihong0618/bilingual_book_maker:latest `
      --book_name /book/my_book.epub `
      --language zh-hans `
      --use_context session
    ```

A test that needs no key at all, over the free Google route:

```bash
docker run --rm \
  -v "$PWD":/book \
  ghcr.io/yihong0618/bilingual_book_maker:latest \
  --book_name /book/animal_farm.epub \
  --api_format google \
  --test \
  --test_num 1 \
  --language zh-hant
```

## A PDF, per system

The `latest` image has no Pandoc and no PDF packages, so it cannot run `--to-epub`. Build the `pdf` image first (above), then:

=== "Linux with NVIDIA"

    Install the NVIDIA driver and the NVIDIA Container Toolkit on the host. Nothing else: PyTorch's wheels carry the CUDA runtime.

    ```bash
    docker run --rm --gpus all \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      bbook_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --use_context session
    ```

=== "Windows with NVIDIA"

    Use Docker Desktop on the **WSL2 backend**. Install the WSL-capable NVIDIA driver on Windows itself, not inside WSL. Windows-containers mode cannot reach the GPU.

    ```powershell
    docker run --rm --gpus all `
      -v "${PWD}:/book" `
      -v bbm-models:/root/.cache `
      -e OPENAI_API_KEY `
      bbook_maker:pdf `
      --book_name /book/paper.pdf `
      --to-epub `
      --use_context session
    ```

=== "CPU only"

    The same image runs on the processor. `--device cpu` skips the accelerator detection. The output is the same; it is slower.

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      bbook_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

=== "macOS"

    The container is CPU-only whatever you pass: Docker runs a Linux VM that cannot see Metal. On Apple silicon the [native install](installation-pdf.md) is both faster (MPS) and much smaller, so prefer it.

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      bbook_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

## GPU only on amd64

PyPI's PyTorch is a CUDA build only on x86_64:

| torch 2.7.1, Linux | |
|---|---|
| `manylinux_2_28_x86_64` | 821.0 MB — the CUDA build |
| `manylinux_2_28_aarch64` | 98.9 MB — no CUDA kernels |

So on an arm64 Linux host that *does* have a card (GH200, Jetson), a plain build gives an arm64 image that runs on the processor, however many `--gpus` you pass. Build and run the x86_64 image there:

```bash
docker build --platform linux/amd64 --target pdf -t bbook_maker:pdf .
```

```bash
docker run --rm --platform linux/amd64 --gpus all \
  -v "$PWD":/book \
  -v bbm-models:/root/.cache \
  -e OPENAI_API_KEY \
  bbook_maker:pdf \
  --book_name /book/paper.pdf \
  --to-epub
```

## What is not in either image

The Codex route (`--api_format codex`). It drives a `codex` binary signed in on your machine; neither the binary nor the login is in the container. Use an API route in Docker.

## Build the small image yourself

A plain build gives the `latest` image's contents:

```bash
docker build -t bbook_maker .
```
