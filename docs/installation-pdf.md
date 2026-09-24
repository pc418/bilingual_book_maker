# Installing the PDF extra

`--to-epub` reads a PDF with [docling](https://github.com/docling-project/docling)'s layout and table models. So it needs more than the base install: PyTorch, the models, and Pandoc to build the EPUB. None of it is installed by default, because most people translate EPUBs and never open a PDF.

No Java is needed. Earlier versions of this route ran a Java engine; it was retired in favor of docling.

## 1. Get the code

The route is not in the published package yet, so you install from a checkout.

```bash
git clone https://github.com/yihong0618/bilingual_book_maker.git
```

```bash
cd bilingual_book_maker
```

```bash
pip install -r requirements.txt
```

A virtual environment is recommended. This is the base install. It cannot read a PDF yet.

## 2. Pandoc

You need Pandoc **3.1.12 or newer** on PATH. It builds the EPUB and its navigation. Older releases point the table of contents at files instead of headings, and the tool refuses them before the PDF is opened. The apt packages on Ubuntu 24.04 and Debian 13 are older than this, so take a release from [pandoc.org/installing.html](https://pandoc.org/installing.html).

```bash
pandoc --version
```

## 3. The PDF packages

Which PyTorch build you get is decided by the *index* you install from, not by the version. Pick your system:

=== "macOS (Apple silicon)"

    ```bash
    pip install -r requirements-pdf-gpu.txt
    ```

    You get MPS acceleration. There is nothing to choose.

=== "Linux with NVIDIA"

    ```bash
    pip install -r requirements-pdf-gpu.txt
    ```

    PyPI's Linux wheel *is* the CUDA build, so this is all you need. The CUDA Toolkit is not needed; the wheel carries the runtime.

=== "Linux, CPU only"

    ```bash
    pip install -r requirements-pdf-cpu.txt
    ```

    The GPU file would give you about 3 GB of CUDA you cannot use. This one is about 180 MB and pulls no `nvidia-*` packages. It names PyTorch's CPU index inside the file, so there is no flag to remember.

=== "Windows with NVIDIA"

    ```bat
    pip install -r requirements-pdf-gpu.txt ^
        --extra-index-url https://download.pytorch.org/whl/cu126
    ```

    **PyPI's Windows wheel is CPU-only.** Windows CUDA builds are published only on PyTorch's own index, so the plain install leaves you on the processor, silently. `cu126` suits the pinned PyTorch 2.7.1; `cu128` is there for newer cards and drivers. The CUDA wheel is about 2.7 GB.

    You also need the NVIDIA driver. PyTorch bundles the CUDA runtime, so you do **not** need the CUDA Toolkit, but the driver is yours to install:

    - Driver download: <https://www.nvidia.com/en-us/drivers/> (Game Ready or Studio both work).
    - PyTorch's installer matrix, to confirm the channel for your card: <https://pytorch.org/get-started/locally/>.

    Check the driver before installing PyTorch:

    ```bat
    nvidia-smi
    ```

    It prints the driver version and the highest CUDA version it supports. If that number is below the channel you chose (12.6 for `cu126`), update the driver. An old driver is the usual reason `torch.cuda.is_available()` says `False` on a machine that has a card.

=== "Windows, CPU only"

    ```bat
    pip install -r requirements-pdf-gpu.txt
    ```

    On Windows this file gives you the CPU build, because that is what PyPI ships there.

Use `--extra-index-url`, never `--index-url`. `--index-url` *replaces* PyPI, and everything else this tool needs would stop resolving.

## 4. Run it

Read two pages first. `--test` translates only a few blocks.

```bash
python make_book.py \
  --book_name paper.pdf \
  --to-epub \
  --pages 1-2 \
  --test
```

Then the full run:

```bash
python make_book.py \
  --book_name paper.pdf \
  --to-epub \
  --use_context session
```

The models (about 500 MB) download on the first run, so the first PDF takes noticeably longer than the second. The progress line keeps running while they download. They come from Hugging Face, so `HF_HOME` moves the cache:

```bash
export HF_HOME=/path/with/room
```

Once cached, extraction needs no network. [PDF to bilingual EPUB](features/pdf-to-epub.md) has the recommended command for each kind of document.

## Check what you got

```bash
python -c "import torch; print(torch.__version__, torch.version.cuda)"
```

- A version ending in `+cpu`, and `None`: the CPU build. Correct for the CPU route.
- A CUDA version like `12.6`: a CUDA build. `torch.cuda.is_available()` then says whether this machine can use it. `False` means the build has CUDA but there is no usable card, or the driver is too old.
- On Apple silicon, `None` is correct: MPS is not CUDA. Check it with `python -c "import torch; print(torch.backends.mps.is_available())"`.
- On Windows with an NVIDIA card, `None` means you are on the CPU build, which is what PyPI ships there. Go back to step 3.

`--device cuda` tells these two failures apart, because they have different fixes: a CPU-only build is a reinstall; a machine without a card is not.

## Installing without a clone: not yet

`pip install "bbook_maker[pdf]"` does **not** install the route, and it does not fail either. pip treats an unknown extra as a warning, installs the last release without docling, and exits 0:

```text
WARNING: bbook-maker 1.2.1 does not provide the extra 'pdf'
Successfully installed bbook-maker-1.2.1
```

The next PDF run refuses with the missing-extra message, which names this command as the one that will not work. Until the route is released, clone the repository (step 1). From a clone, `pip install ".[pdf]"` is equivalent to the requirements files but takes whatever versions resolve today; the files are the pinned, tested set.

### With uv

uv takes the first index that has a package, so it must be told to compare them:

```bash
uv pip install -r requirements-pdf-cpu.txt --index-strategy unsafe-best-match
```

Or use uv's own PyTorch switch:

```bash
uv pip install -r requirements-pdf-gpu.txt --torch-backend=cpu
```

### Keeping the CPU build

`torch==2.7.1+cpu` satisfies any `torch>=…` requirement, so nothing forces it to be replaced. But the next `pip install -U` that touches PyTorch without the index will fetch the CUDA wheel and pull in about 3 GB. Make the index stick to the environment:

```bash
export PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
```

Or in `pip.conf` / `pip.ini`:

```ini
[global]
extra-index-url = https://download.pytorch.org/whl/cpu
```

The uv equivalents are `UV_TORCH_BACKEND=cpu`, or `UV_INDEX_STRATEGY=unsafe-best-match` with `UV_EXTRA_INDEX_URL`.

## Or skip all of it: Docker

The `pdf` image tag carries Pandoc and the whole docling runtime. See [Docker](docker.md), including why a Mac should install natively instead.

## Sizes

What the PDF step downloads, measured 2026-09-21 by resolving the route's dependency tree on each platform (102 packages) with PyTorch pinned as the lock pins it, 2.7.1:

| | download |
|---|---|
| macOS, Apple silicon | **~270 MB** |
| Linux x86_64, CPU build | **~380 MB** |
| Linux x86_64, CUDA build | **~3.2 GB** |
| Windows x86_64, PyPI — the CPU build | **~420 MB** |
| Windows x86_64, `cu126` | **~2.9 GB** |

Plus **~500 MB of models** on the first run, on every platform.

PyTorch is most of the variation. The rest of the tree is about 200 MB everywhere (opencv 48 MB, scipy 29 MB, rapidocr 27 MB, transformers, numpy, pandas…). The PyTorch wheel alone:

| | torch 2.7.1 wheel |
|---|---|
| macOS arm64 | 68.6 MB |
| Linux x86_64, `+cpu` | 175.8 MB, and its metadata declares **no** `nvidia-*` requirements |
| Linux x86_64, PyPI default | 821.0 MB, **plus ~2.16 GB** of `nvidia-*` and `triton` wheels |
| Windows x86_64, PyPI | 216.0 MB — the CPU build |
| Windows x86_64, `cu126` | 2.72 GB |

The two platforms are opposites, which is the trap: on Linux the default is CUDA and you opt *out*; on Windows the default is CPU and you opt *in*. macOS has no CUDA variant; its small PyTorch carries Metal kernels and no CUDA, and nothing is missing.

## If it does not work

These are the lines the tool prints, and what to do.

- **`reading a PDF needs the pdf extra, which is not installed.`** Step 3 was not done. The message carries the install line. If you ran `pip install "bbook_maker[pdf]"` and it said it succeeded, that is the trap described above. Do step 3.
- **`Pandoc is required for --to-epub. Install it and make sure pandoc is on PATH.`** Do step 2.
- **`… is too old for EPUB export; Pandoc 3.1.12 or newer is required`** Your Pandoc came from apt. Install the release from pandoc.org (step 2). The message also mentions `--pandoc PATH`; that option belongs to the staged tool `tools/pdf_to_book.py`, not to `make_book.py`, so put the new Pandoc on PATH instead.
- **`--device cuda was asked for, but the installed PyTorch is a CPU-only build.`** Reinstall with `requirements-pdf-gpu.txt` (and the CUDA index on Windows).
- **`--device cuda was asked for, but this machine has no cuda accelerator available.`** The build has CUDA; the machine or driver cannot provide it. Run `nvidia-smi`: no output means no driver; a CUDA version lower than your channel means the driver is too old. Otherwise use `--device cpu`.
- **On Windows, `torch.version.cuda` is `None` although the machine has a card.** The plain install was used. Reinstall naming the `cu126` index (step 3).
- **`… selected pages have no text layer …; rerun with --pdf-ocr …`** The PDF is a scan. Add `--pdf-ocr`, and `--ocr-lang` if the scan is not in Chinese or English. See [PDF to bilingual EPUB](features/pdf-to-epub.md#what-can-go-wrong).
- **In Docker, `--gpus all` seems ignored on an ARM machine.** It is: the arm64 image has a CPU-only PyTorch. Add `--platform linux/amd64`.
- **In Docker on a Mac, the GPU is never used.** Correct and unfixable: the Linux VM cannot see Metal. Install natively (steps 1 to 4) for MPS.
