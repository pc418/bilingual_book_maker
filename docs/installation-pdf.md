# Installing the PDF reading edition

`--to-epub` reads a PDF with [docling](https://github.com/docling-project/docling)'s
layout and table models, so it needs more than the base install: PyTorch, the
models, and Pandoc to build the EPUB. None of it is installed by default,
because most people who use this tool translate EPUBs and never open a PDF.

No Java is needed. Earlier versions of this route ran a Java engine; it was
retired in favour of docling.

## 1. Get the code

```sh
git clone https://github.com/yihong0618/bilingual_book_maker.git
cd bilingual_book_maker
pip install -r requirements.txt
```

A virtual environment is recommended. This is the base install — it cannot
read a PDF yet.

## 2. Pandoc

**3.1.12 or newer**, on PATH. It builds the EPUB and its navigation; older
releases point the table of contents at files instead of headings, which is
refused after the translation has been paid for. The apt packages on Ubuntu
24.04 and Debian 13 are older than this, so take a release from
[pandoc.org/installing.html](https://pandoc.org/installing.html).

```sh
pandoc --version        # 3.1.12 or newer
```

## 3. The PDF packages — pick one

There are only two cases, because PyPI's PyTorch **is** the GPU build:

### Apple Silicon, NVIDIA GPU, or Windows

```sh
pip install -r requirements-pdf-gpu.txt
```

This is the plain install. On Apple Silicon it gives you MPS; on Linux or
Windows with an NVIDIA card it gives you CUDA; on Windows without one it is
simply the normal wheel. Every CUDA package in that file is marked
`platform_system == "Linux" and platform_machine == "x86_64"`, so outside
Linux nothing CUDA is downloaded and the file is the same install as the CPU
one below.

On Linux you also need an NVIDIA driver new enough for the CUDA runtime in the
wheel; PyTorch ships the CUDA libraries, not the driver.

### Linux without an NVIDIA GPU

```sh
pip install -r requirements-pdf-cpu.txt
```

This is the only case where the choice matters. The file above would give you
~3 GB of CUDA you cannot use; this one is about 180 MB and pulls no
`nvidia-*` packages at all. It names PyTorch's CPU index inside the file, so
there is no flag to remember.

## 4. Run it

```sh
# a first look: extract, translate only a few blocks, then read source.md
python make_book.py --book_name paper.pdf --to-epub --key ${key} --test

# the full run
python make_book.py --book_name paper.pdf --to-epub --key ${key} --use_context session

# a scanned PDF, in Chinese
python make_book.py --book_name scan.pdf --to-epub --pdf-ocr --ocr-lang ch_sim,en --key ${key} --use_context session
```

The models (~500 MB) download on the first run, so the first PDF takes
noticeably longer than the second. The progress line keeps running while they
download. They come from Hugging Face, so `HF_HOME` moves the cache:

```sh
export HF_HOME=/path/with/room
```

Once cached, conversion needs no network.

## Check what you got

```sh
python -c "import torch; print(torch.__version__, torch.version.cuda)"
```

- ends in `+cpu`, and `None` — the CPU build. Correct for the CPU route.
- a version like `12.6` — a CUDA build. `torch.cuda.is_available()` then says
  whether this machine can actually use it; `False` means the build has CUDA
  but there is no usable card or the driver is too old.
- on Apple Silicon, `None` is correct: MPS is not CUDA. Check it with
  `python -c "import torch; print(torch.backends.mps.is_available())"`.

`--device cuda` distinguishes these two failures, because they have different
fixes: a CPU-only build is a reinstall, a machine without a card is not.

## Installing the published package instead

If you would rather not clone:

```sh
pip install "bbook_maker[pdf]"

# Linux without an NVIDIA GPU — the index has to be named on the command line,
# since there is no requirements file to carry it:
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

### Keeping the CPU build

`torch==2.7.1+cpu` satisfies any `torch>=…` requirement, so nothing forces it
to be replaced — but the next `pip install -U` that touches PyTorch without the
index will quietly fetch the CUDA wheel and pull in ~3 GB. Make the index
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

## Sizes

Wheel downloads for the pinned PyTorch (2.7.1), measured 2026-09-21. The
models are a separate ~500 MB on the first run, whichever route you took.

| | PyTorch download |
|---|---|
| Linux x86_64, CPU build | **176 MB**, and no `nvidia-*` packages |
| Linux x86_64, CUDA build | **821 MB**, plus ~2.16 GB of `nvidia-*` and `triton` wheels |
| macOS, Apple Silicon | 69 MB |
| Windows x86_64 | 216 MB |

So on Linux the choice is roughly 180 MB against 3 GB. On macOS and Windows
there is no CUDA variant and nothing to choose.

## If it does not work

- **`reading a PDF needs the pdf extra`** — step 3 was not done. The message
  carries the install line.
- **`--device cuda was asked for, but the installed PyTorch is a CPU-only build`**
  — reinstall with `requirements-pdf-gpu.txt`.
- **`--device cuda was asked for, but this machine has no cuda accelerator`** —
  the build has CUDA; the machine or driver cannot provide it. Use
  `--device cpu`.
- **pages have no text layer** — the PDF is a scan. Add `--pdf-ocr`, and
  `--ocr-lang` if it is not in English, Spanish, French or German.
- **Pandoc too old** — see step 2.
