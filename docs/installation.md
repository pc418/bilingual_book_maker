# Installation
## pip
bilingual_book_maker has been published as a [Python package](https://pypi.org/project/bbook-maker/) and can be install by `pip`. (Recommend in a virtual environment.)
```sh
pip install -U bbook_maker
```

The PDF route (`--to-epub`) is an extra, so a plain install carries none of it. It also needs a Java runtime (11 or newer, a JRE is enough) and Pandoc on PATH:
```sh
pip install -U "bbook_maker[pdf]"
```
Its OCR backend (`--with-ocr`) is a second extra, several gigabytes with torch:
```sh
pip install -U "bbook_maker[ocr]"
```

## git
You can also install from github if you want to use the latest version.
```sh
git clone git@github.com:yihong0618/bilingual_book_maker.git
pip install .
# or the pinned set; the PDF route and its OCR backend are separate files
pip install -r requirements.txt
pip install -r requirements-pdf.txt   # --to-epub
pip install -r requirements-ocr.txt   # --with-ocr (includes the pdf set)
```