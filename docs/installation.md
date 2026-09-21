# Installation
## pip
bilingual_book_maker has been published as a [Python package](https://pypi.org/project/bbook-maker/) and can be install by `pip`. (Recommend in a virtual environment.)
```sh
pip install -U bbook_maker
```

The PDF route (`--to-epub`) also needs a Java runtime (11 or newer, a JRE is enough) and Pandoc 3.1.12 or newer on PATH (Ubuntu 24.04 and Debian 13 apt ship older releases; take the release from https://pandoc.org/installing.html). Its OCR backend (`--with-ocr`) is an extra, several gigabytes with torch, so it is not installed by default:
```sh
pip install -U "bbook_maker[ocr]"
```

## git
You can also install from github if you want to use the latest version.
```sh
git clone git@github.com:yihong0618/bilingual_book_maker.git
pip install .
# or the pinned set, plus the OCR backend if you need --with-ocr
pip install -r requirements.txt
pip install -r requirements-ocr.txt
```