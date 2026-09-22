# Installation
## pip
bilingual_book_maker has been published as a [Python package](https://pypi.org/project/bbook-maker/) and can be install by `pip`. (Recommend in a virtual environment.)
```sh
pip install -U bbook_maker
```

The PDF route (`--to-epub`) is an extra, because it brings docling and PyTorch. It also needs Pandoc 3.1.12 or newer on PATH (Ubuntu 24.04 and Debian 13 apt ship older releases; take the release from https://pandoc.org/installing.html). No Java is needed.

**It is not in the published package yet**, so install it from a checkout (see [git](#git) below). `pip install "bbook_maker[pdf]"` does not fail on the unknown extra — it warns and installs the release without the route, which then still refuses to read a PDF.
```sh
pip install -r requirements-pdf-gpu.txt
```
The two platforms are opposites: on Linux the default wheel is the CUDA build, so without an NVIDIA GPU add PyTorch's CPU index or download about 3.2 GB, most of it CUDA you cannot use; on Windows the default wheel is CPU-only, so *with* an NVIDIA GPU you must add the CUDA index (and install the NVIDIA driver). macOS has nothing to choose. **[installation-pdf.md](installation-pdf.md) gives the exact command for each case**, and the uv equivalents.

## git
You can also install from github if you want to use the latest version.
```sh
git clone git@github.com:yihong0618/bilingual_book_maker.git
pip install .
# or the pinned set, plus the PDF route if you want --to-epub
pip install -r requirements.txt
pip install -r requirements-pdf-cpu.txt   # or requirements-pdf-gpu.txt for CUDA
```