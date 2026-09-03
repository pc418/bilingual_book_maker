# Contributing

## Scope: public domain books only

The tool is for books in the public domain and for files you hold the
rights to translate. See the first paragraph of the README and
[disclaimer.md](./disclaimer.md).

## DRM: not supported, not going to be

An EPUB carrying DRM (Adobe ADEPT, Readium LCP, Apple FairPlay, or any
`encryption.xml` beyond font obfuscation) is refused before a single
request leaves the machine. There is no flag to bypass the check and none
will be added.

Issues and pull requests that ask for DRM removal, link to DRM-removal
tools, or add circumvention code are closed without discussion. The
template maintainers use:

> This project does not remove or bypass DRM and will not add support for
> it. Translate a DRM-free copy of the book instead. Closing.

Do not name DRM-removal tools anywhere in the repository, including issue
comments.

## Everything else

- Run `black book_maker tests` before pushing; CI checks it first.
- Run the suite: `python -m pytest -q`.
- Keep a PR to one change. Tests go with the code they cover.
