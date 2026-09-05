"""Token-budget general grouping + the json_object capability degree.

The seven contracts from
``docs/260904-plan-GENERAL_GROUPING_AND_JSON_OBJECT_DEGREE.md`` (Stage 2),
plus the four amendments its "Stage 1 results" section pinned after the
260905 off-OpenAI eval:

1. the batch json-degree parse takes ``extract_json_object`` *plus* a
   required-top-key check, and the classifier's rungs stop failing open;
2. sub-strict degrees carry at most half the strict unit cap per request;
3. the plan gate admits ``strict``/``shape``/``json`` and nothing else;
4. ``ENTRY_RUNG`` has no ``unsupported`` key on purpose — the prompt rung is
   the default, and that is a decision, not an accident.
"""

import json
import shutil
import threading
from itertools import cycle
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup as bs

from book_maker.cli import resolve_plan_mode
from book_maker.loader.plan import (
    GENERAL_GROUP_MAX_UNITS,
    SUBSTRICT_GROUP_MAX_UNITS,
    DisplayResolver,
    assign_batches,
    partition_soup,
)
from book_maker.structured import extract_json_object, schema_required_keys
from book_maker.translator.base_translator import BatchMismatch
from book_maker.translator.capabilities import ENTRY_RUNG, CapabilityLedger
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    batch_field_name,
    single_field_name,
)

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ANIMAL_FARM = REPO / "test_books" / "animal_farm.epub"

LANGUAGE = "Chinese"
BATCH_FIELD = batch_field_name(LANGUAGE)
SINGLE_FIELD = single_field_name(LANGUAGE)


# --------------------------------------------------------------- fixtures


def _units(body_html):
    soup = bs(f"<html><body>{body_html}</body></html>", "html.parser")
    return partition_soup(soup, DisplayResolver([]), "chap.xhtml").units


def _paragraph(words, tag="p"):
    return f"<{tag}>{' '.join(['word'] * words)}.</{tag}>"


def _completion(content, finish_reason="stop"):
    message = SimpleNamespace(content=content, refusal=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def _translator(create=None, parse=None):
    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.model = "test-model"
    translator.model_list = None
    translator.keys = cycle(["k"])
    translator.temperature = 1.0
    translator.extra_body = {}
    translator.context_flag = False
    translator.context_list = []
    translator.context_translated_list = []
    translator.context_paragraph_limit = 0
    translator.system_content = ""
    translator.prompt_sys_msg = ""
    translator.prompt_template = ChatGPTAPI.DEFAULT_PROMPT
    translator.language = LANGUAGE
    translator.source_language = None
    translator._api_lock = threading.Lock()
    translator.capabilities = CapabilityLedger()
    translator._rung_refusals = {}
    translator.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=create or Mock(return_value=_completion("plain")),
                parse=parse or Mock(),
            ),
        )
    )
    return translator


def _reply(texts, ids=None, field=BATCH_FIELD, item_field=SINGLE_FIELD):
    ids = range(len(texts)) if ids is None else ids
    return json.dumps(
        {field: [{"id": i, item_field: t} for i, t in zip(ids, texts)]},
        ensure_ascii=False,
    )


# ------------------------------------ 1. assign_batches with a token budget


class TestTokenBudgetGrouping:
    def test_mixed_lengths_pack_to_the_budget(self):
        # every unit is far too long for the short-run rule (>= 70 chars),
        # which is the whole point: a budget groups *any* consecutive units
        units = _units("".join(_paragraph(30) for _ in range(12)))
        assert all(u.chars >= 70 for u in units)

        assign_batches(units, token_budget=200)

        groups = {}
        for unit in units:
            assert unit.group_id is not None, "long units must group under a budget"
            groups.setdefault(unit.group_id, []).append(unit)
        assert len(groups) > 1
        for members in groups.values():
            assert len(members) <= GENERAL_GROUP_MAX_UNITS
            assert sum(u.token_count for u in members) <= 200

    def test_the_unit_cap_bounds_a_generous_budget(self):
        units = _units("".join(_paragraph(20) for _ in range(40)))

        assign_batches(units, token_budget=10**6)

        sizes = {}
        for unit in units:
            sizes[unit.group_id] = sizes.get(unit.group_id, 0) + 1
        assert max(sizes.values()) == GENERAL_GROUP_MAX_UNITS

    def test_a_unit_alone_over_budget_stays_solo(self):
        units = _units(
            _paragraph(4) * 2 + _paragraph(400) + _paragraph(4) * 2,
        )
        assert len(units) == 5

        assign_batches(units, token_budget=60)

        assert units[2].group_id is None
        # and it ends the run around it rather than joining either side
        assert units[0].group_id == units[1].group_id is not None
        assert units[3].group_id == units[4].group_id is not None
        assert units[0].group_id != units[3].group_id

    def test_no_budget_is_byte_identical_to_the_short_run_grouping(self):
        html = (
            "<p>Short one</p><p>Short two</p>"
            + _paragraph(60)
            + "<h3>Short three</h3><p>Short four</p><p>Short five</p>"
        )
        with_budget_none = _units(html)
        assign_batches(with_budget_none, token_budget=None)
        default = _units(html)
        assign_batches(default)

        assert [u.group_id for u in with_budget_none] == [u.group_id for u in default]
        # the long paragraph is still solo without a budget
        assert default[2].group_id is None

    def test_deterministic(self):
        html = "".join(_paragraph(n) for n in (5, 40, 7, 90, 12))
        a, b = _units(html), _units(html)

        assign_batches(a, token_budget=300)
        assign_batches(b, token_budget=300)

        assert [u.group_id for u in a] == [u.group_id for u in b]

    def test_the_token_count_is_cached_on_the_unit(self):
        units = _units("".join(_paragraph(10) for _ in range(4)))
        assert all(u.token_count is None for u in units)

        assign_batches(units, token_budget=500)

        assert all(isinstance(u.token_count, int) for u in units)

    def test_the_count_does_not_depend_on_a_model_name(self, monkeypatch):
        # grouping is a property of the book: pinned to cl100k_base, so the
        # same book partitions into the same requests on every endpoint
        import book_maker.loader.plan as plan_mod

        seen = []
        real = plan_mod.num_tokens_from_text

        def spy(text, *args, **kwargs):
            seen.append((args, kwargs))
            return real(text)

        monkeypatch.setattr(plan_mod, "num_tokens_from_text", spy)
        assign_batches(
            _units("".join(_paragraph(9) for _ in range(3))), token_budget=99
        )

        assert seen and all(a == () and k == {} for a, k in seen)


# -------------------------------------------- 2. the loader honors the flag


def _plan_loader(tmp_path, model_cls, **attrs):
    from book_maker.loader.epub_loader import EPUBBookLoader

    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / ANIMAL_FARM.name
    shutil.copy(ANIMAL_FARM, src)
    loader = EPUBBookLoader(
        str(src), model_cls, "dummy-key", resume=False, language="zh-hans"
    )
    loader.plan_mode = True
    loader.translate_tags = "auto"
    loader.plan_classify = "all"
    loader.only_filelist = "index_split_008.html"
    for name, value in attrs.items():
        setattr(loader, name, value)
    return loader, src


class _RecordingModel:
    """Minimal translator that records the size of every batch it is sent."""

    TRANSLATION_ERROR_MARKER = None
    _fatal_error_detected = False
    degree = False

    def __init__(self, key, language, **kwargs):
        self.list_calls = []
        self.single_calls = []

    def _structured_enabled(self):
        return self.degree

    def translate(self, text, needprint=True):
        self.single_calls.append(text)
        return f"T[{text}]"

    def translate_list(self, text_list):
        self.list_calls.append(list(text_list))
        return [f"T[{t}]" for t in text_list]


class _StrictModel(_RecordingModel):
    degree = "strict"


class TestLoaderHonorsAccumulatedNum:
    def test_a_budget_groups_long_paragraphs_and_prints_no_ignore_note(
        self, tmp_path, capsys
    ):
        loader, _ = _plan_loader(tmp_path, _StrictModel, accumulated_num=800)
        loader.make_bilingual_book()

        out = capsys.readouterr().out
        assert "--accumulated_num is ignored" not in out
        model = loader.translate_model
        assert any(len(call) > 1 for call in model.list_calls)
        # ordinary prose, not the short runs plan mode groups on its own
        assert any(
            len(call) > 1 and all(len(t) >= 70 for t in call)
            for call in model.list_calls
        ), model.list_calls

    def test_without_the_flag_only_short_runs_group(self, tmp_path):
        loader, _ = _plan_loader(tmp_path, _StrictModel)
        loader.make_bilingual_book()

        for call in loader.translate_model.list_calls:
            assert all(len(text) < 70 for text in call), call

    def test_a_substrict_endpoint_gets_the_tighter_cap(self, tmp_path):
        # amendment 2: both content regressions the eval found were large
        # batches on endpoints below strict decoding
        loader, _ = _plan_loader(tmp_path, _RecordingModel, accumulated_num=100_000)
        loader.make_bilingual_book()

        sizes = [len(call) for call in loader.translate_model.list_calls]
        assert sizes and max(sizes) <= SUBSTRICT_GROUP_MAX_UNITS

    def test_a_strict_endpoint_keeps_the_full_cap(self, tmp_path):
        loader, _ = _plan_loader(tmp_path, _StrictModel, accumulated_num=100_000)
        loader.make_bilingual_book()

        sizes = [len(call) for call in loader.translate_model.list_calls]
        assert max(sizes) > SUBSTRICT_GROUP_MAX_UNITS
        assert max(sizes) <= GENERAL_GROUP_MAX_UNITS


# ----------------------------------------------- 3. the plan mode gate


class _Probe:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.verdict


@pytest.mark.parametrize(
    "verdict,named",
    [("strict", "strict"), ("shape", "shape"), ("json", "JSON object")],
)
def test_every_admitted_degree_plans_and_names_itself(verdict, named):
    mode, reason = resolve_plan_mode("epub", "openai", False, _Probe(verdict))

    assert mode == "model"
    assert named in reason


@pytest.mark.parametrize("verdict", [False, "unsupported", "request rejected: 400"])
def test_no_structuring_at_all_stays_in_tag_mode(verdict):
    # amendment 3: `unsupported` is the router dropping response_format; a
    # prompt-rung plan entry is a separate decision, not this change
    mode, reason = resolve_plan_mode("epub", "openai", False, _Probe(verdict))

    assert mode == "none"
    assert "schema" in reason


# ------------------------------------ 4. batch translate at the json degree


class TestJsonDegreeBatchTranslate:
    def _translator_at_json(self, create):
        translator = _translator(create=create)
        translator.capabilities.verdicts["test-model"] = "json"
        return translator

    def test_a_fenced_reply_with_the_right_ids_aligns(self):
        create = Mock(
            return_value=_completion(
                "Sure, here you go:\n```json\n" + _reply(["一", "二"]) + "\n```"
            )
        )
        translator = self._translator_at_json(create)

        assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]
        assert create.call_args.kwargs["response_format"] == {"type": "json_object"}
        assert "json_schema" not in json.dumps(create.call_args.kwargs)

    def test_the_described_schema_travels_in_the_prompt(self):
        create = Mock(return_value=_completion(_reply(["一", "二"])))
        translator = self._translator_at_json(create)

        translator._do_structured_batch_translate(["a", "b"])

        content = create.call_args.kwargs["messages"][-1]["content"]
        assert BATCH_FIELD in content
        assert "single JSON object" in content
        # the target language stays the last thing the model reads
        assert content.rstrip().endswith(f"{LANGUAGE}.")

    def test_unparseable_json_is_a_batch_mismatch_for_the_loader(self):
        create = Mock(return_value=_completion("I am afraid I cannot do that."))
        translator = self._translator_at_json(create)
        translator.translate = Mock(side_effect=lambda t, _=True: f"t:{t}")

        with pytest.raises(BatchMismatch):
            translator._do_structured_batch_translate(["a", "b"])

        # one attempt, no per-item sweep: the loader's ladder halves instead
        assert create.call_count == 1
        assert translator.translate.call_count == 0

    def test_a_missing_top_key_is_a_batch_mismatch_not_a_fall_through(self):
        # amendment 1: `extract_json_object` alone would hand back the first
        # object it can parse, whatever it is
        create = Mock(
            return_value=_completion(json.dumps({"translations": ["一", "二"]}))
        )
        translator = self._translator_at_json(create)

        with pytest.raises(BatchMismatch):
            translator._do_structured_batch_translate(["a", "b"])

    def test_a_stray_object_in_the_prose_is_stepped_over(self):
        create = Mock(
            return_value=_completion(
                'Note: {"about": "I translated these"}\n' + _reply(["一", "二"])
            )
        )
        translator = self._translator_at_json(create)

        assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]

    def test_wrong_ids_are_a_batch_mismatch(self):
        create = Mock(return_value=_completion(_reply(["一", "二"], ids=[0, 7])))
        translator = self._translator_at_json(create)

        with pytest.raises(BatchMismatch):
            translator._do_structured_batch_translate(["a", "b"])

    def test_an_empty_tail_slot_is_a_batch_mismatch(self):
        # amendment 2's other half: the eval's empty-tail corruption is what
        # the existing empty-slot check is for, at this degree too
        create = Mock(return_value=_completion(_reply(["一+二 merged", ""])))
        translator = self._translator_at_json(create)

        with pytest.raises(BatchMismatch):
            translator._do_structured_batch_translate(["a", "b"])

    def test_an_oversized_batch_is_refused_before_the_request(self):
        # codex P1: the loader sizes batches for the model current at plan
        # build, but --model_list rotation can hand the batch to a
        # json-degree model whose cap is 8. The translator refuses before
        # spending a request; the loader's ladder halves the batch.
        from book_maker.loader.plan import SUBSTRICT_GROUP_MAX_UNITS

        n = SUBSTRICT_GROUP_MAX_UNITS + 1
        create = Mock(return_value=_completion(_reply(["x"] * n)))
        translator = self._translator_at_json(create)

        with pytest.raises(BatchMismatch, match="json-degree cap"):
            translator._do_structured_batch_translate([f"p{i}" for i in range(n)])
        assert create.call_count == 0

    def test_a_batch_at_the_json_degree_cap_goes_through(self):
        from book_maker.loader.plan import SUBSTRICT_GROUP_MAX_UNITS

        n = SUBSTRICT_GROUP_MAX_UNITS
        create = Mock(return_value=_completion(_reply([f"t{i}" for i in range(n)])))
        translator = self._translator_at_json(create)

        assert translator._do_structured_batch_translate(
            [f"p{i}" for i in range(n)]
        ) == [f"t{i}" for i in range(n)]

    def test_a_strict_endpoint_still_gets_a_schema(self):
        parse = Mock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            parsed=SimpleNamespace(
                                **{
                                    BATCH_FIELD: [
                                        SimpleNamespace(
                                            **{"id": 0, SINGLE_FIELD: "一"}
                                        ),
                                        SimpleNamespace(
                                            **{"id": 1, SINGLE_FIELD: "二"}
                                        ),
                                    ]
                                }
                            ),
                            refusal=None,
                            content=None,
                        ),
                        finish_reason="stop",
                    )
                ]
            )
        )
        create = Mock()
        translator = _translator(create=create, parse=parse)
        translator.capabilities.verdicts["test-model"] = "strict"

        assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]
        assert parse.call_count == 1
        assert create.call_count == 0

    def test_a_shape_endpoint_batches_with_the_schema(self):
        # the batch schema pins no *values* (the language rides in the field
        # name and the prose), so shape decoding is enough for it
        translator = _translator()
        translator.capabilities.verdicts["test-model"] = "shape"

        assert translator._structured_enabled() == "shape"

    @pytest.mark.parametrize("verdict", [False, "unsupported"])
    def test_no_structuring_falls_back_to_the_delimiter_method(self, verdict):
        translator = _translator()
        translator.capabilities.verdicts["test-model"] = verdict

        assert translator._structured_enabled() is False


# ---------------------------------------- 5. the classifier from "json"


class TestClassifierAtTheJsonDegree:
    def test_the_ladder_starts_at_json_object_and_classifies(self):
        create = Mock(
            return_value=_completion(
                '```json\n{"p.header": {"content_type": "running head", '
                '"verdict": "skip"}}\n```'
            )
        )
        translator = _translator(create=create)
        translator.capabilities.verdicts["test-model"] = "json"
        schema = {
            "name": "classify",
            "schema": {
                "type": "object",
                "properties": {"p.header": {"type": "object"}},
                "required": ["p.header"],
            },
        }

        result = translator.structured_json("classify these", schema)

        assert result == {
            "p.header": {"content_type": "running head", "verdict": "skip"}
        }
        assert (
            translator.structured_rungs("classify these", schema)[0][0] == "json_object"
        )
        assert create.call_args_list[0].kwargs["response_format"] == {
            "type": "json_object"
        }

    def test_an_object_that_answers_nothing_descends_instead_of_passing(self):
        # amendment 1: the fail-open. An unescaped quote leaves the outer
        # object unparseable and the scan finds an inner fragment; without a
        # key check that fragment came back as the answer.
        schema = {
            "name": "classify",
            "schema": {
                "type": "object",
                "properties": {"p.header": {"type": "object"}},
                "required": ["p.header"],
            },
        }
        create = Mock(
            side_effect=[
                # the outer object does not parse (the stray quote), and the
                # scan's next candidate is the *inner* one — an object that
                # answers nothing
                _completion(
                    '{"p.header": {"verdict": "skip"}, '
                    '"p.foot": {"verdict": "sk"ip"}}'
                ),
                _completion('{"p.header": {"verdict": "skip"}}'),
            ]
        )
        translator = _translator(create=create)
        translator.capabilities.verdicts["test-model"] = "json"

        assert translator.structured_json("classify", schema) == {
            "p.header": {"verdict": "skip"}
        }
        assert create.call_count == 2

    def test_required_keys_come_from_the_schema(self):
        schema = {
            "name": "classify",
            "schema": {
                "type": "object",
                "properties": {"a": {}, "b": {}},
                "required": ["a", "b"],
            },
        }
        assert schema_required_keys(schema) == ("a", "b")
        # no `required`: the declared properties are what an answer carries
        assert schema_required_keys({"schema": {"properties": {"x": {}}}}) == ("x",)
        assert schema_required_keys({"schema": {}}) == ()

    def test_a_schema_echo_is_still_recovered(self):
        # the required-key check must not cost us the measured echo case
        echoed = json.dumps(
            {"type": "object", "properties": {"p.header": {"verdict": "skip"}}}
        )
        assert extract_json_object(echoed, ("p.header",)) is not None

    def test_partial_answers_still_count(self):
        # "lacks *every* expected key" is the bar, not "lacks one"
        obj = extract_json_object(json.dumps({"a": 1}), ("a", "b"))
        assert obj == {"a": 1}


def test_entry_rung_defaults_to_the_prompt_for_an_unsupported_endpoint():
    # amendment 4: the absence of an "unsupported" key is the decision —
    # an endpoint that produced no JSON at all is asked in prose
    assert "unsupported" not in ENTRY_RUNG
    assert ENTRY_RUNG.get("unsupported", "prompt") == "prompt"

    translator = _translator()
    translator.capabilities.verdicts["test-model"] = "unsupported"
    assert translator.structured_rungs("q", {"schema": {}})[0][0] == "prompt"


# ------------------------------------------------- 6. the plan records it


class TestPlanMetaRecordsTheBudget:
    def _plan(self, tmp_path, token_budget):
        from ebooklib import epub

        from book_maker.loader.plan import build_plan

        book = epub.read_epub(str(ANIMAL_FARM))
        return build_plan(book, token_budget=token_budget)

    def test_the_budget_is_recorded_and_changes_the_plan_identity(self, tmp_path):
        src = tmp_path / ANIMAL_FARM.name
        tmp_path.mkdir(parents=True, exist_ok=True)
        shutil.copy(ANIMAL_FARM, src)

        none = self._plan(tmp_path, None).plan_meta(str(src))
        small = self._plan(tmp_path, 400).plan_meta(str(src))
        large = self._plan(tmp_path, 4000).plan_meta(str(src))

        assert none["token_budget"] is None
        assert small["token_budget"] == 400
        assert small != large
        assert small != none

    def test_the_stored_shape_still_reads_as_this_schema(self, tmp_path):
        from book_maker.loader.plan import PLAN_SCHEMA_VERSION

        tmp_path.mkdir(parents=True, exist_ok=True)
        src = tmp_path / ANIMAL_FARM.name
        shutil.copy(ANIMAL_FARM, src)
        plan = self._plan(tmp_path, 800)
        path = tmp_path / "plan.json"
        plan.save_json(str(path), book_path=str(src))

        data = json.loads(path.read_text())
        # additive meta only: the budget changes which units share a request,
        # never which units exist, so no resume cache or row key moves
        assert data["schema_version"] == PLAN_SCHEMA_VERSION
        assert data["token_budget"] == 800

    def test_the_budget_is_not_a_planning_setting(self, tmp_path):
        # same reason `poetry_group_size` is not one: it decides how many
        # units share a request, never what a row's evidence says, so a
        # changed budget must not reopen a fully decided plan
        from book_maker.loader.plan import planning_settings

        assert "token_budget" not in planning_settings(("sup", "code"))
