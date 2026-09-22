
## 260921: README Features section (`1c5fc33`)

Owner: "Get plan mode, session mode, pdf to epub from cli tags to different
section under features grouping with lower layer H tags, explain how and when
they should be used and caveats." Done in `README.md` only: a `## Features`
section between Usage and Params with `### Plan mode`, `### Session mode`,
`### PDF to EPUB`, each as what it does / when to use it / commands / flags /
caveats. The caveats were taken from `COMPAT_RULES` in `cli.py`
(A4, A7, A8, A10, A13, C5, C18, C26, C27, `PARALLEL_SESSION_REFUSAL`),
`_plan_mode_conflict`, the `--help` text and the corpus record. The three
Params entries are one-paragraph pointers so `test_cli_documentation.py`
still finds every long option (it caught `--only_filelist` /
`--exclude_filelist` the first time). `docs/cmd.md` untouched. `README-CN.md` mirrored in `7eb08fa` (owner: "cn also."), written by the lead, not machine-translated; it also fixed the stale `--context-compact-at` minimum (`500` → `1500`).

`5c86630` (owner: "add java dependency and a link of how to install at pdf
section and a (experimental) tag at pdf to epub with pr/issues welcom"): both
READMEs, the PDF subsection heading is `PDF to EPUB (experimental)` /
`PDF 转 EPUB (实验性)` (ASCII parens so the GitHub anchor is predictable;
the Params pointers link `#pdf-to-epub-experimental` / `#pdf-转-epub-实验性`),
the requirements bullet says Java 11 or newer (OpenDataLoader's own README and
`java/pom.xml` compiler target 11) with the Adoptium link, Pandoc's install
page, the pip extras, and that a missing one is refused before the PDF is
opened (`opendataloader.py` `shutil.which("java")` before the converter,
`to_epub.py` `find_pandoc` before the bundle). A closing paragraph invites
issues and PRs with the PDF or the failing `source.md` page.

`0f6750c` (owner: "append this pic at last of both md", a screenshot of the
SoL-Pi reading edition in Apple Books: TOC from the headings, bilingual
text, figure as a picture): saved as `docs/img/pdf_reading_edition.webp`
(1600×1052, 119 KB, from the 2000×1315 PNG via Pillow, beside the other
`docs/img/*.webp`), placed as the last item of the PDF to EPUB subsection in
both READMEs, after the experimental note. Read "at last" as the end of the
PDF section, not the end of the file (which is the appreciation QR code);
one line to move if the owner meant the file's end.

`50f899d` (owner: "mark **bilingual** epub at pdf section and explain pdf is
hard to organize directly, so this is already a good one."): both READMEs,
**bilingual** bolded in the subsection's opening sentence with "every
paragraph followed by its translation", and an expectations paragraph before
the screenshot: a PDF is a page description (glyphs at positions, no
paragraphs/headings/columns/reading order), extractors guess the structure
back, a reflowable bilingual EPUB with a working TOC is already a good
result, a heading one level off or a table as prose is the format's limit and
a minute's edit in `source.md`.

`7e2fb49` (owner: "pdf to **bilingual** epub"): the subsection heading is
`### PDF to **bilingual** EPUB (experimental)` / `### PDF 转 **双语** EPUB
(实验性)`; GitHub strips the asterisks from the slug, so the Params pointers
now link `#pdf-to-bilingual-epub-experimental` / `#pdf-转-双语-epub-实验性`.

260921 push (owner: "push and draft pr msg and the link at docs"): pre-push
checks `black --check` clean, branch base == `main` == `upstream/main`
(`3f7fc1e`), no `docs/2*.md`/`plan.md`/LICENSE in `git diff main..HEAD`,
full suite `2377 passed, 172 skipped, 3 xfailed` (250 s). Pushed with the
pc418 ceremony (`gh-login.sh` → `git push -u origin feat/pdf-markdown-epub`
→ `gh auth logout --user pc418`, facilec active again). Codex review not
rerun: the code was reviewed through `ccd0b42`, everything since is README.
PR body, title and compare link:
`docs/260921-docs-PR_BODY_PDF_TO_BILINGUAL_EPUB.md`. Flagged there and to
the owner: `opendataloader-pdf[hybrid]` as a base dependency pulls torch,
CUDA wheels, transformers and docling into `requirements.txt` (48 → 161
packages) for every `pip install`; the 260920 owner decision was "hybrid
remains a project dependency", the alternative is a `bbook_maker[pdf]`
extra. The odl-probe venv from 260919 is gone from `/private/tmp`, so the
pdfium-backed tests skipped in this session's run (146 in the PDF files);
their last real-pdfium run is in the 260920 fix record.

`b82b795` (owner 260921, five rulings in one batch): the PDF route is the
`pdf`/`ocr` extras, nothing in the base install; socksio declared; CI opts
in and smokes the engine; Docker root + `ocr` tag; skill §1e. Full record:
`docs/260921-fix-PDF_DEPS_OCR_EXTRA.md`. Runs on the final revision: repo
interpreter `tests/test_cli_documentation.py` + `test_pdf_dependencies.py`
6 passed, `black --check .` clean; CI-like scratch venv (hash-pinned
`requirements.txt` + `requirements-pdf.txt`, `pip install . pytest`, the
260919 scratch Pandoc on PATH, real pdfium): `2524 passed, 27 skipped, 3
xfailed`, one failure = the documentation test on `--gpus`/`--version`
strings, fixed after that run started and green on rerun. Earlier venv
runs on the way: 205 failures without socksio (this machine's
`all_proxy=socks5`), 1 failure without Pillow (`No module named 'PIL'` in
the sanitizer), 3 failures at the interim state (the same documentation
test in three files). Codex scoped review launched on
`7e2fb49^..b82b795` before the push (scratch `review-deps/`).

Codex review of `7e2fb49^..b82b795` (thread `01a0c600-98bb-7310-bc1b-a55ee0e8fe45`,
verdict needs-attention, "the extras split itself is reasonable"): high,
`docker.yaml` — metadata-action's default flavor adds `latest` to any
semver tag, for both matrix entries, so a release could publish the OCR
image as `latest`; fixed in `c410925` with `flavor: latest=false` (rolling
tags are the explicit raw rule only; releases get `<version>` and
`<version>-ocr`). Medium, `pyproject.toml` — the `[[tool.pdm.autoexport]]`
entry (base only, `without-hashes = true`) could not reproduce the three
committed files and an ordinary lock update would rewrite the base file
without hashes; `c410925` adds `tools/export_requirements.sh` (lock
`--update-reuse -G :all`, three hash-pinned exports), and after the
resumed re-verify ("partially resolved") `d7bbce5` removes the entry.
Re-verify: Docker resolved, no additional breakage. Pushed `d7bbce5` to
the pc418 fork with the ceremony; facilec active again.

`7ac0096` (owner pasted the image workflow's failure: `failed to calculate
checksum of ref …: "/requirements-ocr.txt": not found`): `.dockerignore` is
an allowlist (`*` then `!book_maker`, `!make_book.py`, `!requirements.txt`),
so the new file never entered the build context and the ocr stage's COPY
failed. Added `!requirements-ocr.txt`. Not caught here because there is no
Docker daemon on this machine, which the record already said; a local
`docker build --target ocr .` would have shown it. Pushed.

`f976df3` (owner: "is requirements-pdf.txt heavy?" → 3 packages, ~31 MB →
"Then put it in requirements.txt"): `opendataloader-pdf`, `pypdfium2`,
`pillow` moved into the base list; the `pdf` extra and
`requirements-pdf.txt` removed (`.gitignore`/`.dockerignore` entries
too); `requirements-ocr.txt` is the ocr group alone (139); base export 49
packages (main 48: + the three + socksio, − brotli/brotlicffi/socksio's
stale copies). `PDF_ROUTE_NOT_INSTALLED` now says the packages are base
dependencies. CI drops the pdf install step. README/README-CN, cmd.md,
installation.md, skill §1e follow. Verified: `tools/export_requirements.sh`
(lock groups default+ocr), `black --check .` clean, CI-like venv
reinstalled from the base file: pipeline + dependency + documentation
tests `176 passed, 1 skipped` with real pdfium and Pandoc; repo
interpreter `6 passed`. Pushed.

`c70bf0f` (owner: "fix ci"): upstream PR #574's `testing` job failed
every export test with `EPUB navigation is invalid: entry 1 … points at
text/ch001.xhtml rather than at the heading #chapter-one`. Cause: the
job's apt Pandoc is 3.1.3 (ubuntu-24.04); the heading fragment in the
EPUB contents arrived in Pandoc 3.1.12 (bisected 3.1.11 fail / 3.1.12
pass with downloaded releases). Debian 13, the Docker base, ships 3.1.11,
so the ocr image had the same defect unbuilt. Fix: `find_pandoc` refuses
a Pandoc below 3.1.12 by version (`PANDOC_TOO_OLD`, before the PDF is
opened); CI and the Dockerfile ocr stage install the 3.11 release .deb
from GitHub (Docker: `ADD` with `TARGETARCH`); README/README-CN,
installation.md, skill §1e and failure table state the floor;
`tests/test_pandoc_version.py` pins the gate; the test helper now fails
loudly on an old Pandoc instead of skipping. Whole suite in the CI-like
venv with Pandoc 3.11: `2536 passed, 27 skipped, 3 xfailed`; black clean.
Record: `docs/260921-fix-PANDOC_MIN_VERSION_CI.md`. Pushed.

`bc300af` (owner: "how should macos with mps use?" → "is it in readme and
skill?"): README/README-CN `--no-gpu` bullet and skill §1e now name the
detected accelerators (CUDA on NVIDIA, MPS on Apple silicon from a native
install, no flag; Docker on a Mac is CPU-only). Doc test 3 passed. #574 CI
at `c70bf0f` all green: testing 5m59s, core and ocr image builds (ocr
21m49s), typos. Pushed.

`ef3e157` (owner: "Does the pdf support page selection" → add it, update
README and skill, test mid-range on the arXiv paper with Opus, push):
`--pages` on the main CLI (1-based; parsed before the PDF is opened; own
bundle and book names `<stem>_pages-6-7_…` so a chapter never overwrites
the whole book; compat row C28). The worker's first live run found that a
mid-section start leaves prose above the first heading and the export
refuses the nav after the translation was paid; the extractor now writes
`# Page N` into source.md for such a selection (terminal line + manifest
limitation). Rerun from clean: exit 0, EPUB beside the PDF, nav exactly
the five headings with fragments, containment of pages 6-7 only,
EPUBCheck clean, rerun reuses extraction and translation. Suite 2554
passed in the CI-like venv, black clean, Google cell exit 0. Codex review
not run (quota). Record: `docs/260921-feat-PDF_PAGES_FLAG.md`. Pushed.

`9f4df65` (owner: "get the ocr lang passed to the opendataloader pdf"):
`--ocr-lang` on the main CLI and the harness, passed to the backend's own
`--ocr-lang` (EasyOCR codes; the engine's default is en, es, fr, de).
Recorded in the manifest and the extraction job; a scanned page met
without the flag prints the default-languages line and records it as a
limitation; a rerun with other languages reads a scan again, a typed
document is left alone; compat row C29. The live smoke on a two-page
Chinese scan found three defects on the way: the backend launcher was
taken from PATH (a pyenv shim without docling won over the venv the
converter came from), the language list was split twice (`--ocr-lang
"['ch_sim','en']"`), and docling's refusal never reached the operator
because the log was not drained on the failure path. The end-to-end run
then found a fourth, in the export: a front-matter line's *translation*
was measured against the 80-character banner limit, so the nav check
refused the book after the translation was paid for. All four fixed and
pinned. Google cell exit 0, EPUB read back (nav fragment, 8 translation
divs, no residue), EPUBCheck 0/0/0. Codex review not run (quota until
25 Sep). #574 all four checks green on this commit (testing 6m40s, ocr
image 21m19s). Record: `docs/260921-feat-PDF_OCR_LANG_FLAG.md`. Pushed.
