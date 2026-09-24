# Installation

bilingual book maker is a Python program. It needs Python 3.10 or newer. A virtual environment keeps it apart from your other packages.

## From PyPI

```bash
pip install -U bbook_maker
```

This gives you the `bbook_maker` command. It translates EPUB, TXT, Markdown and SRT files, and PDFs on the older text route.

The published package does not carry the PDF-to-EPUB route yet. For that, install from a checkout (below) and add the [PDF extra](installation-pdf.md).

## From a checkout

Use a checkout when you want the newest code or the PDF-to-EPUB route.

```bash
git clone https://github.com/yihong0618/bilingual_book_maker.git
```

```bash
cd bilingual_book_maker
```

```bash
pip install -r requirements.txt
```

`requirements.txt` is the pinned, tested set. `pip install .` also works and takes whatever versions resolve today. From a checkout the command is `python make_book.py`, with the same flags as `bbook_maker`.

## The PDF extra

`--to-epub` reads a PDF with docling's layout and table models. It needs PyTorch, the models (about 500 MB, downloaded on the first run) and Pandoc 3.1.12 or newer. None of this is in the base install.

```bash
pip install -r requirements-pdf-gpu.txt
```

That line is right on Apple silicon, on Linux with an NVIDIA GPU, and on Windows without an NVIDIA GPU. Linux without an NVIDIA GPU should use `requirements-pdf-cpu.txt` instead, or it downloads about 3 GB of CUDA it cannot use. Windows with an NVIDIA GPU needs PyTorch's CUDA index. [PDF extra](installation-pdf.md) has the command for every case.

Do not run `pip install "bbook_maker[pdf]"`. The published package has no such extra yet. pip only warns and installs the release without the route.

## Docker

If you would rather not install Python packages, use the published image. See [Docker](docker.md).

## Check the install

```bash
bbook_maker --help
```

From a checkout:

```bash
python make_book.py --help
```

The help text is the authority on every flag. [Command line options](cmd.md) lists them in one table.
