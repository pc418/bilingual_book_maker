# PDF sanitizer fix re-verification

Reviewed 2026-09-20 at HEAD `01edec9`, concentrating on the changes in
`3dab126` and the five files requested by the owner. No implementation
changes were made. Existing suites were not rerun; focused synthetic-PDF
and Pandoc-AST probes exercised the disputed behavior.

## Environment and evidence

- pypdfium2 5.13.0 / PDFium 153.0.7999.0, from the Codex runtime dependency
  directory. `FPDFPage_InsertObjectAtIndex` is available.
- Pandoc: `../scratchpad/pdf-md-epub-260919/bin/pandoc`, with the pipeline's
  `markdown-markdown_in_html_blocks` reader.
- Synthetic files and output bundles used temporary directories, removed
  after the probes. Fixture variants were constructed in memory from
  `tests/pipeline_helpers.py`; that file was not edited.

## Seven requested fixes

1. **Rotation and empty renders:** rotation now holds. The clipped fixture
   produced the same 802-by-78 replacement at 0, 90, 180, and 270 degrees.
   An injected empty render returned `changed=False` and marked the form
   `kept`. Crop handling remains incomplete: a CropBox extending beyond
   MediaBox yielded no image for the same visible figure. `_page_box`
   needs the effective inherited MediaBox/CropBox intersection (`get_bbox`).
2. **Clip-path intersection:** holds for the rectangular reproducer. One
   clip and two successive clips both reported 331 hidden / 19 visible
   characters; the previous implementation lost the hidden count.
3. **Leaf clips:** detection holds; moving the clip into the form's text
   still reported 331 hidden / 19 visible. However, direct text objects
   with any visibility below 50% are now deleted outright. A 15-character
   run with 22.57% visible was removed, including its visible glyphs.
4. **Eight-drawing guard:** implemented, but does not protect ordinary
   page wrappers. A full-page Form with normal margins, eight horizontal
   separator rules, and 1,030 visible prose characters was classified as
   `figure`, with zero hidden characters. Ink bounds occupied about 63%
   of the page, so the 70% wrapper guard did not apply.
5. **Insertion index:** holds on the pinned runtime. Object types stayed
   `[text, image, text]` after replacing `[text, form, text]`. The fallback
   for older PDFium still appends. White flattening remains an independent,
   unresolved compositing change; preserving order does not preserve alpha.
6. **PDFium errors and stage failure:** holds for a real injected
   `PdfiumError` from examination. The adapter reported `page 1 could not
   be examined: PdfiumError: injected examination failure` and recorded
   the extraction stage as failed.
7. **Scan heuristic:** works for the fixture's top-level full-page image
   plus a page-number stamp. The identical scan inside a Form produces
   picture share 0 and no missing pages, versus share 1 and missing page 1
   for the top-level image. Consequently it bypasses OCR_REQUIRED and can
   select `--no-ocr` even with OCR requested.

## Additional confirmed issues

- **Inherited CropBox changes classification:** a page cropped to
  `[0, 0, 320, 320]` with the box on its page dictionary was left alone.
  Moving the same box to the Pages ancestor made `get_cropbox()` return
  `[0, 0, 612, 792]`, although `get_bbox()` still returned the effective
  320-square page. The sanitizer then flagged its 190 visible characters
  as a figure. Effective page geometry must be shared by detection and
  rendering.
- **Marker shield is incomplete:** `1. 引言` and `(a) 方法` become paragraphs,
  and a genuine source bullet list stays a list. But `#. 方法` remains an
  OrderedList. Source `A. Smith wrote this.` is a Pandoc paragraph, yet
  the source-prefix shortcut suppresses shielding of `1. 引言`, which
  becomes an OrderedList. `2.1. 方法` already parses as a paragraph and
  is not evidence of a working shield.
- **Kept objects are reported as rasterized:** `_rasterized_lines` checks
  the detection reason, not `rasterized`/`kept`. An empty-render result
  can therefore claim successful remediation while the original hidden
  text remains available to extraction.

## Test adequacy and design advice

The added adapter tests still mock sanitization and text-layer detection.
The failure test injects an already-normalized PipelineError, not a raw
PDFium error. Real fixtures check a top-level scan, zero drawings, and an
oversized wrapper; they do not constrain nested scans, normal-margin page
wrappers, partly visible text, inherited boxes, or marker parsing. The
replacement-image test still permits an arbitrary blank image.

Prefer deleting only text proven fully invisible. Keep visible prose and
identify the smallest self-contained figure, rather than equating path
count with semantic figure identity. Compute image coverage through
nested transforms and clips in effective page coordinates, without
double-counting overlapping images. Distinguish detected, changed, and
unresolved objects in diagnostics. Apply reader dark-mode backgrounds at
the presentation layer rather than whitening the sanitized source PDF.
Use Pandoc structure to decide whether a source block is actually a list.

Assessment: **needs attention**; the narrow fixes improve the original
reproducers but still permit silent content loss and missed scans.

## Scoped follow-up at e6647b3

The owner requested only six fix checks. Focused probes on PDFium 5.13.0
confirmed partially visible page-level text is retained while wholly
invisible text is reported, nested scan coverage is 1.0 and triggers scan
classification, the inherited CropBox wrapper is retained, and kept
figures emit FIGURE_KEPT rather than a rasterization claim. Capped summed
image coverage is an accepted choice; marker shielding is an accepted
unfixed limitation, not reopened here. No full suite was rerun.

One protection is incomplete: a form with 480 visible and 320 hidden
characters is retained without drawings, but adding the fixture's eight
rules flags the same form as `figure`. The hidden-text guard fails and
falls through to `visible < 600` in the drawing rule. Thus the new
`test_a_form_showing_more_text_than_it_hides_is_left_as_text` passes only
with `figure_draws=False` and misses this interaction. For a substantial
overflow (`hidden >= 200`) with more shown than hidden text, apply the
body-preservation veto before the drawing rule and add the eight-rule
variant. This is an incompletely fixed loss path, not a new independent
regression. No other new material regression was established in scope.

Final scoped check: the substantial-overflow body veto now precedes both
classification branches. The new eight-rule variant pins the bypass,
while the mostly-hidden positive case remains. The fix holds by diff and
test inspection; no new regression identified in this change.

Ratio-rule follow-up: replayed the 1,030-visible-character/eight-rule
prose-wrapper probe with FIGURE_CHARS_PER_DRAWING=40; figure_report
remains empty (128.75 characters per drawing exceeds 40). A likely
real-world false positive is a ruled invoice/table section wrapped in a
sub-page Form: many border paths and short cell values satisfy the ratio
without making the content a chart. This counterexample is a design
inference, not a corpus reproduction; add a negative table fixture.
