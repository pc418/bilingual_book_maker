# Installation
## pip
bilingual_book_maker has been published as a [Python package](https://pypi.org/project/bbook-maker/) and can be install by `pip`. (Recommend in a virtual environment.)
```sh
pip install -U bbook_maker
```

The PDF route (`--to-epub`) is an extra, because it brings docling and PyTorch. It also needs Pandoc 3.1.12 or newer on PATH (Ubuntu 24.04 and Debian 13 apt ship older releases; take the release from https://pandoc.org/installing.html). No Java is needed.
```sh
pip install -U "bbook_maker[pdf]"
```
On Linux without an NVIDIA GPU, add PyTorch's CPU index or you will download about 3 GB of CUDA you cannot use — on macOS and Windows there is nothing to choose. **[installation-pdf.md](installation-pdf.md) gives the exact command for CPU, CUDA and MPS**, and the uv equivalents.

## git
You can also install from github if you want to use the latest version.
```sh
git clone git@github.com:yihong0618/bilingual_book_maker.git
pip install .
# or the pinned set, plus the PDF route if you want --to-epub
pip install -r requirements.txt
pip install -r requirements-pdf-cpu.txt   # or requirements-pdf-gpu.txt for CUDA
```