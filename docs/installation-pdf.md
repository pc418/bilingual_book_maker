# Installing the PDF reading edition

`--to-epub` reads a PDF with [docling](https://github.com/docling-project/docling)'s
layout and table models, so it needs more than the base install: PyTorch, the
models, and Pandoc to build the EPUB. None of it is installed by default,
because most people who use this tool translate EPUBs and never open a PDF.

Three things are being chosen here, and they are separate:

| | what it decides |
|---|---|
| the `pdf` extra | whether this tool can read a PDF at all |
| which PyTorch build | how much you download — **on Linux only** |
| `--device` | where the models run, per run |

`--device cpu` is fully supported and produces the same text as a GPU run. It
is slower, and that is the whole difference.

## Before you start

**Pandoc 3.1.12 or newer**, on PATH. It builds the EPUB and its navigation;
older releases point the table of contents at files instead of headings, which
is refused after the translation has been paid for. The apt packages on Ubuntu
24.04 and Debian 13 are older than this, so take a release from
[pandoc.org/installing.html](https://pandoc.org/installing.html).

```sh
pandoc --version        # 3.1.12 or newer
```

No Java is needed. Earlier versions of this route ran a Java engine; it was
retired in favour of docling.

## macOS (Apple Silicon) — MPS

Nothing to choose. The PyTorch wheel on PyPI is already CPU/MPS only; there are
no CUDA wheels for macOS, and the CPU index below serves the identical file.

```sh
pip install "bbook_maker[pdf]"
# from a checkout:
pip install -r requirements.txt -r requirements-pdf-cpu.txt
```

`--device auto` resolves to `mps` on Apple Silicon. `--device cpu` forces the
processor. Both work; MPS is faster.

## Linux or Windows with an NVIDIA GPU — CUDA

PyPI's PyTorch **is** the CUDA build, so this is the plain install:

```sh
pip install "bbook_maker[pdf]"
# from a checkout:
pip install -r requirements.txt -r requirements-pdf-gpu.txt
```

On Linux this pulls PyTorch plus its CUDA wheels — about **1.8 GB**. You also
need an NVIDIA driver new enough for the CUDA runtime in that wheel; PyTorch
ships the CUDA libraries, not the driver.

Check that you got what you wanted:

```sh
python -c "import torch; print(torch.version.cuda, torch.cuda.is_available())"
```

`None` for the first value means a CPU-only build is installed — reinstall via
the CUDA route. `False` for the second means the build has CUDA but this
machine cannot use it (no card, or a driver too old). `--device cuda` reports
these two cases as two different errors, because they have two different fixes.

## Linux without an NVIDIA GPU — CPU

This is the case where the choice matters. The default PyPI wheel would give
you ~1.8 GB of CUDA you cannot use; the CPU build is about **200 MB** and pulls
no `nvidia-*` packages at all.

```sh
# from a checkout -- the file names the index itself, so there is no flag
# to remember:
pip install -r requirements.txt -r requirements-pdf-cpu.txt

# installing the published package instead:
pip install "bbook_maker[pdf]" --extra-index-url https://download.pytorch.org/whl/cpu
```

Use `--extra-index-url`, not `--index-url`: `--index-url` *replaces* PyPI, and
everything else this tool needs would stop resolving.

With **uv**, the flag alone is not enough — uv takes the first index that has a
package, so it must be told to compare them:

```sh
uv pip install "bbook_maker[pdf]" \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --index-strategy unsafe-best-match

# or, simpler, uv's own PyTorch switch:
uv pip install "bbook_maker[pdf]" --torch-backend=cpu
```

Confirm no CUDA came along:

```sh
python -c "import torch; print(torch.__version__)"   # ends in +cpu
pip list | grep nvidia                                # prints nothing
```

### Keeping the CPU build

`torch==2.14.0+cpu` satisfies any `torch>=…` requirement, so nothing forces it
to be replaced — but the next `pip install -U` that touches PyTorch without the
index will quietly fetch the CUDA wheel and overwrite ~1.6 GB. Make the index
stick to the environment:

```sh
export PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
```

or in `pip.conf` / `pip.ini`:

```ini
[global]
extra-index-url = https://download.pytorch.org/whl/cpu
```

The uv equivalents are `UV_TORCH_BACKEND=cpu`, or `UV_INDEX_STRATEGY=unsafe-best-match`
with `UV_EXTRA_INDEX_URL`.

## The models

The models are downloaded on first use, not at install time — roughly **500 MB**,
separate from everything above. The first PDF you convert will therefore take
noticeably longer than the second; the progress line keeps running while it
downloads.

They come from Hugging Face, so `HF_HOME` moves the cache:

```sh
export HF_HOME=/path/with/room
```

Once they are cached, conversion needs no network.

## Sizes, end to end

Linux x86_64, Python 3.12, PyTorch 2.14.0, measured 2026-09-21:

| | download | models, first run |
|---|---|---|
| base install (no PDF) | — | — |
| `pdf`, CPU build | ~200 MB | ~500 MB |
| `pdf`, CUDA build | ~1.8 GB | ~500 MB |

On macOS and Windows the PyTorch wheel is ~125 MB and there is no CUDA variant,
so the CPU/GPU distinction does not apply.

## If it does not work

- **`reading a PDF needs the pdf extra`** — the extra is not installed. The
  message carries the install line.
- **`--device cuda was asked for, but the installed PyTorch is a CPU-only build`**
  — reinstall through the CUDA route above.
- **`--device cuda was asked for, but this machine has no cuda accelerator`** —
  the build has CUDA; the machine or driver cannot provide it. Use `--device cpu`.
- **pages have no text layer** — the PDF is a scan. Add `--pdf-ocr`, and
  `--ocr-lang` if it is not in English, Spanish, French or German.
- **Pandoc too old** — see the top of this page.
