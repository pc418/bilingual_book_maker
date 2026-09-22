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

## 3. The PDF packages

```sh
pip install -r requirements-pdf-gpu.txt
```

That is the whole step for **Apple Silicon** (you get MPS), for **Linux with
an NVIDIA GPU** (you get CUDA, because PyPI's Linux wheel *is* the CUDA build)
and for **Windows without an NVIDIA GPU**.

Two cases need something else. Both are on the same principle: which PyTorch
build you get is decided by the *index* you install from, not by the version.

### Linux without an NVIDIA GPU

```sh
pip install -r requirements-pdf-cpu.txt
```

The plain file would give you ~3 GB of CUDA you cannot use; this one is about
180 MB and pulls no `nvidia-*` packages. It names PyTorch's CPU index inside
the file, so there is no flag to remember.

### Windows with an NVIDIA GPU

**PyPI's Windows wheel is CPU-only** — unlike Linux, Windows CUDA builds are
published only on PyTorch's own index. So the plain install above leaves you
on the processor, silently. Name the CUDA channel:

```sh
pip install -r requirements-pdf-gpu.txt ^
    --extra-index-url https://download.pytorch.org/whl/cu126
```

(`cu126` suits the pinned PyTorch 2.7.1; `cu128` is there for newer cards and
drivers. The CUDA wheel is about 2.7 GB.) Use `--extra-index-url`, not
`--index-url` — see the note in the appendix.

**You also need the NVIDIA driver**, and this is the part people miss. PyTorch
bundles the CUDA *runtime* inside its wheel, so you do **not** need the CUDA
Toolkit — but the driver is yours to install:

- Driver download: **<https://www.nvidia.com/en-us/drivers/>** (pick your card;
  either the Game Ready or the Studio driver works)
- Or install it with GeForce Experience / the NVIDIA App if you already have one
- PyTorch's own installer matrix, if you want to confirm the channel for your
  card: **<https://pytorch.org/get-started/locally/>**

Verify the driver is present and new enough before installing PyTorch:

```sh
nvidia-smi
```

That prints the driver version and the highest CUDA version it supports. If
that number is below the channel you chose (12.6 for `cu126`), update the
driver — an old driver is the usual reason `torch.cuda.is_available()` comes
back `False` on a machine that plainly has a card.

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
- on Windows with an NVIDIA card, `None` means you are on the CPU build —
  which is what PyPI ships there. Go back to step 3.

`--device cuda` distinguishes these two failures, because they have different
fixes: a CPU-only build is a reinstall, a machine without a card is not.

## Installing the published package instead

If you would rather not clone:

```sh
pip install "bbook_maker[pdf]"

# Linux without an NVIDIA GPU — the index has to be named on the command line,
# since there is no requirements file to carry it:
pip install "bbook_maker[pdf]" --extra-index-url https://download.pytorch.org/whl/cpu

# Windows with an NVIDIA GPU — PyPI's Windows wheel is CPU-only, so CUDA has
# to be asked for (and the NVIDIA driver installed; see step 3):
pip install "bbook_maker[pdf]" --extra-index-url https://download.pytorch.org/whl/cu126
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
| Linux x86_64, CUDA build (PyPI default) | **821 MB**, plus ~2.16 GB of `nvidia-*` and `triton` wheels |
| Windows x86_64, PyPI — **this is the CPU build** | **216 MB** |
| Windows x86_64, `cu126` | **2.7 GB** |
| Windows x86_64, `cu128` | **3.3 GB** |
| macOS, Apple Silicon | 69 MB |

So on Linux the choice is roughly 180 MB against 3 GB, and on Windows 216 MB
against 2.7 GB. macOS has no CUDA variant and nothing to choose.

The two platforms are opposites, which is the trap: on Linux the default is
CUDA and you opt *out*; on Windows the default is CPU and you opt *in*.

## If it does not work

- **`reading a PDF needs the pdf extra`** — step 3 was not done. The message
  carries the install line.
- **`--device cuda was asked for, but the installed PyTorch is a CPU-only build`**
  — reinstall with `requirements-pdf-gpu.txt`.
- **`--device cuda was asked for, but this machine has no cuda accelerator`** —
  the build has CUDA; the machine or driver cannot provide it. Run
  `nvidia-smi`: no output at all means no driver
  (<https://www.nvidia.com/en-us/drivers/>), and a CUDA version lower than the
  channel you installed means the driver is too old. Otherwise use
  `--device cpu`.
- **On Windows, `torch.version.cuda` is `None` although the machine has a
  card** — the plain install was used. PyPI's Windows wheel is CPU-only;
  reinstall naming the `cu126` index (step 3).
- **pages have no text layer** — the PDF is a scan. Add `--pdf-ocr`, and
  `--ocr-lang` if it is not in English, Spanish, French or German.
- **Pandoc too old** — see step 2.
