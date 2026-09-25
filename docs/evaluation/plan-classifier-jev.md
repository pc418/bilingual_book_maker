# Can a dedicated classifier decide the plan? Jev against gpt-5.6-luna

## Abstract

Plan mode asks a model, for each kind of block in an EPUB, whether it is worth translating. That is a labeling job, not a writing job, so a dedicated classifier could do it. Jev, TypeSafe's System One classifier, answers typed multiple-choice questions with a confidence. The same 31 signatures of the test book were classified by Jev and by gpt-5.6-luna. They agreed on 27. All 4 disagreements were Jev choosing `skip` where luna chose `translate`, each at low confidence. Jev used about twice luna's prompt tokens. Verdict: Jev works as a classify model; the default stays the translating model.

## Setup

- **Book:** `test_books/animal_farm.epub`, the repository's test book. Its plan has 31 tag signatures.
- **Run:** `--test --test_num 8 --quiet --language zh-hans`, translating with gpt-5.6-luna, under a 1500 MB memory cap.
- **Luna arm:** `--model gpt-5.6-luna --classify-model gpt-5.6-luna`. The endpoint verifies a strict JSON schema, so the signatures go in pages of several per request.
- **Jev arm:** `--model gpt-5.6-luna --classify-model typesafe-ai/jev --classify-base-url https://ai-gateway.vercel.sh/typesafe`. One Choice question per signature. Below 0.5 confidence in its top answer, Jev answers `unsure`.
- **Fixed-engine cell:** `--api_format google --classify-model gpt-5.6-luna`, to check that a machine-translation route gets a plan from an LLM classifier.
- **Compared:** each signature's verdict between the two arms. Nobody judged which verdict was right.

## Results

Copied from the record:

| cell | classification | classifier usage line |
|---|---|---|
| luna | 31 signatures in 3 requests, 4 skips | `Classifier (gpt-5.6-luna at the endpoint's default host): tokens: in 7.6k, out 1.8k, cached 0 (3 requests)` |
| Jev | one 503 retry, then 31 verdicts, 8 skips | `Classifier (typesafe-ai/jev at https://ai-gateway.vercel.sh/typesafe): tokens: in 14.4k, out 1.1k, cached 0 (3 requests)` |

**27 of 31 agree.** All 4 disagreements are Jev choosing skip where luna chose translate, and all 4 were Jev's low-confidence calls (0.58–0.61):

| Signature | Sample text |
|---|---|
| `inline:span.underline` | www.ericseat.com |
| `inline:span.calibre_4` | www.ericseat.com |
| `inline:span.calibre_16` | "M" |
| `inline:span.calibre3` | George Orwell |

Where the two agree, Jev's confidence is mostly above 0.9. It used about twice luna's prompt tokens (14.4k vs 7.6k), since each signature is sent as its own question.

**Fixed engine with an LLM classifier.** Google Translate with gpt-5.6-luna as the classifier: `llm classification: 31 verdict(s), 4 skip(s)`, `Translation plan: … coverage 99.8%`, and the classifier's line `Classifier (gpt-5.6-luna …): tokens: in 7.6k, out 1.6k (3 requests)`. The translations sat beside their originals and matched them (George Orwell → 乔治·奥威尔).

A second Jev run, after the key-routing fixes, retried three 503s and ended with `llm classification: 31 verdict(s), 8 skip(s)` and `Translation plan: 20 documents, 202560 chars, coverage 99.7%`.

## Decision

- Jev is available as a classify model: `--classify-model jev` at TypeSafe's own address, or a gateway's id with `--classify-base-url` and `--classify-key`. See [Provider file and extra models](../providers.md#jev-a-dedicated-classifier).
- The default classifier stays the translating model.
- The abstain threshold stays at 0.5. With two options it fires only on a tie. At 0.62 the four disagreements above would become `unsure`, which stops a run in `model` mode. The four skips were recorded, not judged: two URLs, a single letter and a bare author name, which a reading edition could keep or translate.
- The doubled prompt tokens come from each question repeating the whole per-signature prompt. Sending the shared instructions once is a later optimization.

What would change it: a comparison on more books, with verdicts judged against what a reader wants, and a price for both arms.

## Limits

- One book, 31 signatures, one run per arm.
- The key was a gateway key, so TypeSafe's own address was not exercised live (it answered 401 to that key).
- Token counts only; no money comparison.
- Agreement with luna is not correctness: luna's verdicts were not judged either.

Source: docs/260923-feat-ENDPOINT_OVERRIDES_CLASSIFIER_JEV.md (repository, dated records)
