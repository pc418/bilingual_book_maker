<div align="left">

# Bilingual Book Maker

**中文 | [English](./README.md)**

bilingual_book_maker 是一个 AI 翻译工具，使用 ChatGPT 帮助用户制作多语言版本的 epub/txt/md/srt/pdf 文件和图书。请仅将其用于您有权翻译的内容——您持有必要权利的作品、许可或授权允许您翻译的作品、公有领域图书，或适用法律另行允许的使用方式。请在使用之前阅读项目的 **[免责声明](./disclaimer.md)**。

[![Stars](https://img.shields.io/github/stars/yihong0618/bilingual_book_maker)](https://github.com/yihong0618/bilingual_book_maker/stargazers)
[![CI](https://github.com/yihong0618/bilingual_book_maker/actions/workflows/make_test_ebook.yaml/badge.svg)](https://github.com/yihong0618/bilingual_book_maker/actions/workflows/make_test_ebook.yaml)
[![PyPI](https://img.shields.io/pypi/v/bbook-maker.svg)](https://pypi.org/project/bbook-maker/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![litellm](https://img.shields.io/badge/%20%F0%9F%9A%85%20liteLLM-OpenAI%7CAzure%7CAnthropic%7CPalm%7CCohere%7CReplicate%7CHugging%20Face-blue?color=green)](https://github.com/BerriAI/litellm)

</div>

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)


**文档站：** <https://yihong0618.github.io/bilingual_book_maker/>。同样的页面也在本仓库的 [`docs/`](docs/index.md) 下（英文）：先看[快速开始](docs/quickstart.md)，再看你的文件对应的页面——[EPUB](docs/formats/epub.md)、[PDF](docs/features/pdf-to-epub.md)、[TXT](docs/formats/txt.md)、[SRT](docs/formats/srt.md) 或 [Markdown](docs/formats/md.md)。

## 安装

需要 Python 3.10 或更新版本，以及一个模型的 API key（OpenAI 或 Anthropic [^token]、任意 OpenAI 兼容接口，或本地模型），或者一个免费的机器翻译路由。

```shell
pip install -U bbook_maker          # 得到 bbook_maker 命令
# 或者从克隆的仓库安装（最新代码，也是使用 PDF 路由的唯一方式）：
pip install -r requirements.txt     # 然后运行 python3 make_book.py
```

更多：[安装](docs/installation.md)、[Docker](docs/docker.md)。

## 快速开始

仓库里带了一本示例书 `test_books/animal_farm.epub`。`--test` 只翻译开头几段，出错几乎不花钱。

```shell
export OPENAI_API_KEY=sk-...
```

**EPUB**——在输入文件旁边写出 `animal_farm_bilingual.epub`：

```shell
python3 make_book.py --book_name test_books/animal_farm.epub --use_context session --test
```

然后看 [EPUB](docs/formats/epub.md) 和 [EPUB 推荐设置](docs/features/recommended-epub.md)。

**TXT 文件**——写出 `the_little_prince_bilingual.txt`：

```shell
python3 make_book.py --book_name test_books/the_little_prince.txt --batch_size 20 --test
```

SRT 和 Markdown 用法相同：[TXT](docs/formats/txt.md)、[SRT](docs/formats/srt.md)、[Markdown](docs/formats/md.md)。

**PDF**——见下面的 [PDF 转双语 EPUB](#pdf-转双语-epub实验性)。

**或者交给 coding agent**（仓库自带对应的 skill）：

```shell
git clone https://github.com/yihong0618/bilingual_book_maker.git
cd bilingual_book_maker
codex "你好，请用 bbm-plan 把这本书 test_books/animal_farm.epub 翻译成中英双语版，谢谢。"
```

## 支持的接口

路由是一个接口，不是一个模型名：`--api_base`（地址）、`--key`，以及 `--model`（接口上模型 id 的原样拼写）。用 OpenAI 官方 API 时省略 `--api_base`，用 `gpt-5.6-luna` 时省略 `--model`。无法推断时用 `--api_format` 指明请求格式。也可以把接口写进 `bbm_providers.json`，之后只传 `--provider NAME`：[Provider 文件](docs/providers.md)。

| 服务 | 怎么用 |
|---|---|
| OpenAI | 默认；`OPENAI_API_KEY` |
| Anthropic Claude | `--model claude-sonnet-4-6`；`ANTHROPIC_API_KEY` |
| 任意 OpenAI 兼容接口（OpenRouter、硅基流动、Azure、网关） | `--api_base <url>/v1 --model <id>` |
| Gemini | `--api_format gemini` |
| Qwen-MT | `--api_format qwen` |
| Groq、xAI、LiteLLM 代理 | `--api_format groq`、`xai`、`litellm`，加 `--model` |
| OrcaRouter | `--model orcarouter` |
| Ollama、llama.cpp、vLLM、LM Studio | `--api_base http://localhost:11434/v1 --model <id>`；不需要 key |
| 通过 Codex CLI 使用你的 ChatGPT 订阅 | `--api_format codex` |
| Google 翻译、DeepL、DeepL 免费版、彩云小译、腾讯、自定义 API | `--api_format google`、`deepl`、`deeplfree`、`caiyun`、`tencent`、`customapi` |

每家服务一条命令：[用 LLM 翻译](docs/llm-args.md#one-command-per-vendor) 和 [机器翻译](docs/machine-args.md)。key 与环境变量：[环境变量](docs/env_settings.md)。旧参数（`--model gpt4o`、`--openai_key` 等）仍然可用：[从旧参数迁移](docs/migration.md)。

## 功能

### 计划模式

EPUB 的文字分布在段落、标题、列表项、表格单元格、诗行和图注里。默认情况下，工具会找出它们、按类型分组、让模型判断哪些类型值得翻译，并把结果存进 `<book>_plan.json`；之后相邻的块共用一次请求。不需要任何参数。用 `--plan-dry-run` 预览会翻译哪些（不需要 key），用 `--plan-classify all` 全部翻译，或用 `--plan-classify agent` 自己决定计划。详见：[计划模式](docs/features/plan-mode.md)。

### 会话模式

`--use_context session` 整本书保持一个对话，人名、语体和术语从一章到下一章保持一致；在支持提示缓存的接口上，历史的成本很低。历史超过 `--context-compact-at`（默认 8192）时，模型写一份简短的交接报告作为下一个窗口的开头。必须固定的译名用 `--glossary` 指定。详见：[会话模式](docs/features/session-mode.md)。

### PDF 转双语 EPUB（实验性）

`--to-epub` 把 PDF 变成可重排、带目录的双语 EPUB：每段原文后面跟着译文，插图和独立公式保留为图片。它需要从克隆的仓库安装 PDF 扩展（`pip install ".[pdf]"`，会复用你已经装好的 PyTorch；没有 NVIDIA 显卡的 Linux 和有显卡的 Windows 要加 PyTorch 自己的索引，见 [安装 PDF 扩展](docs/installation-pdf.md)），以及 Pandoc 3.1.12 或更新版本。先读两页，打开 `paper_pages-1-2_book/source.md` 检查标题，再为整个文件付费：

```shell
python3 make_book.py --book_name paper.pdf --to-epub --pages 1-2 --test
python3 make_book.py --book_name paper.pdf --to-epub --use_context session
```

扫描件需要 `--pdf-ocr`（非英文扫描件再加 `--ocr-lang iso:zh`、`iso:ja` 之类）；论文加 `--img-model gpt-5.6-luna` 效果更好。不加 `--to-epub` 时，PDF 输出为双语 `.txt`，这条文本路由同样支持 `--use_context session` 和 `--glossary`。详见：[PDF 转双语 EPUB](docs/features/pdf-to-epub.md) 和 [PDF 推荐设置](docs/features/recommended-pdf.md)。

![一篇 arXiv 论文做成的阅读版：由标题生成的目录、双语正文、保留为图片的插图](./docs/img/pdf_reading_edition.webp)

## 参数说明

每个参数一行。完整说明见 [命令行参数](docs/cmd.md)，以 `python3 make_book.py --help` 为准。

**所有格式**

| 参数 | 作用 |
|---|---|
| `--book_name` | 要翻译的文件；扩展名决定格式 |
| `--language` | 目标语言：标签、名称或 `TAG:NAME`（[标签](docs/languages.md)）；默认 `zh-hans` |
| `--source_lang` | 直接指定源语言，不做检测 |
| `--test`、`--test_num` | 只翻译开头几段（默认 10） |
| `--resume` | 继续一次中断的运行 |
| `--single_translate` | 只输出译文，不保留原文 |
| `--prompt` | 自定义提示词：模板、JSON，或 `.json`/`.txt`/`.md` 文件（[提示词文件](docs/prompt.md)） |
| `--batch_size` | TXT、Markdown 和 PDF 文本路由每次请求的行数 |
| `--accumulated_num` | EPUB 计划模式：每次请求的 token 数；SRT：每次请求的字符数 |
| `--parallel-workers` | 同时处理多个 EPUB 章节或 Markdown 批次 |
| `--quiet` | 不显示进度条和段落回显 |

**接口与模型**（[用 LLM 翻译](docs/llm-args.md)）

| 参数 | 作用 |
|---|---|
| `--model` | 模型 id，按接口的原样拼写；默认 `gpt-5.6-luna` |
| `--key`、`--api_key` | API key（同一参数的两个名字）；未给时读 `BBM_API_KEY`，再读该格式自己的变量 |
| `--api_base` | 接口地址 |
| `--api_format` | 无法推断时指定请求格式或机器翻译引擎 |
| `--provider` | `bbm_providers.json` 里的具名接口 |
| `--model_list` | 在多个模型之间轮换 |
| `--proxy` | HTTP 代理 |
| `--temperature` | 采样温度 |
| `--no-thinking` | 让推理模型不先思考 |
| `--extra_body`、`--extra_headers` | 额外的请求字段或 HTTP 头 |
| `--interval` | 请求之间的间隔（仅 gemini 路由） |
| `--batch`、`--batch-use` | OpenAI 的 Batch API（不支持 EPUB） |

**上下文**（[会话模式](docs/features/session-mode.md)）

| 参数 | 作用 |
|---|---|
| `--use_context` | 不带值或 `window`：重发最近几对原文译文；`session`：整本书一个历史 |
| `--context_paragraph_limit` | window 模式：重发多少对 |
| `--context-compact-at` | 会话模式：估计 token 数到此值时压缩（默认 8192） |
| `--no-context-compact` | 会话模式：不写交接报告直接滚动 |
| `--glossary`、`--terminology` | `术语 -> 译名` 文件，翻译必须遵守（同一参数的两个名字） |
| `--glossary-auto` | `on`：保留会话交接报告中确定的译名（默认关闭） |

**EPUB**（[EPUB](docs/formats/epub.md)、[计划模式](docs/features/plan-mode.md)）

| 参数 | 作用 |
|---|---|
| `--plan-classify` | 计划如何决定：`auto`（默认）、`none`、`all`、`model`、`agent` |
| `--plan-dry-run` | 打印并保存计划，不翻译 |
| `--plan-min-coverage` | 计划覆盖的文字比例低于此值时停止（默认 0.5） |
| `--max-batch-units` | 每次请求最多的单元数（默认 16） |
| `--classify-model` | 用另一个模型分类，或用 Jev（`jev`） |
| `--classify-base-url`、`--classify-key` | 该模型的地址和 key |
| `--classify-min-confidence` | Jev 的门槛：把握更低的跳过判断改为翻译（默认 0.95） |
| `--translate-tags` | 没有计划时要翻译的标签（默认 `p`） |
| `--exclude-translate-tags` | 永不翻译的标签（默认 `sup,code`；`""` 表示全部翻译） |
| `--allow_navigable_strings` | 同时翻译不在任何标签里的文字 |
| `--only_filelist`、`--exclude_filelist` | 只翻译 EPUB 里的这些文件，或跳过它们 |
| `--block_size` | 没有计划时：把段落合并成块 |
| `--sentence_mode` | 没有计划时：逐句翻译 |
| `--translation_style`、`--translation_color` | 译文的 CSS，或只给颜色 |
| `--no_disclosure` | 不加那一行 AI 翻译说明 |
| `--translation-metadata` | 在普通标签模式下也写出元数据文件 |
| `--retranslate` | 重新翻译已完成双语 EPUB 的一段（[用法](docs/cmd.md#retranslate-epub-only)） |

**PDF**（[PDF 转双语 EPUB](docs/features/pdf-to-epub.md)）

| 参数 | 作用 |
|---|---|
| `--to-epub` | 把 PDF 做成双语 EPUB |
| `--pdf-ocr` | 识别没有文字层的页面（扫描件） |
| `--ocr-lang` | OCR 引擎要认的语言（`iso:zh`、`iso:ja` 等） |
| `--ocr-engine` | 用哪个 OCR 引擎读扫描件：`auto`、`rapidocr`、`ocrmac`、`easyocr`、`tesseract` |
| `--ocr-replace-layer` | 配合 `--pdf-ocr`：每一页都重新 OCR，丢掉乱码文字层 |
| `--pages` | 只处理这些页（`12-30`、`1,3,5-7`） |
| `--device` | 提取模型运行的设备：`auto`、`cpu`、`cuda`、`mps`、`xpu` |
| `--no-formula-images` | 独立公式保留占位符，不裁成图片 |
| `--pdf-image-dpi N` | 配合 `--to-epub`：插图清晰度，按 PDF 自身页面尺寸计的 DPI；默认 200，标签很小的图用 300；换个值重跑只重绘插图 |
| `--img-model` | 修正区域角色的视觉模型（`none` 关闭） |
| `--img-base-url`、`--img-key` | 该模型的地址和 key |
| `--pdf_layout` | 仅文本路由：同时输出双语 PDF（`top-bottom`、`side-by-side`、`all`） |

## Docker

`docker run --rm -v "$PWD":/book -e OPENAI_API_KEY ghcr.io/yihong0618/bilingual_book_maker:latest --book_name /book/my_book.epub` 翻译挂载目录里的一本书；`pdf` 标签另带 Pandoc 和 PDF 依赖，用于 `--to-epub`。标签、各系统的 GPU 说明和自己构建镜像见 [Docker](docs/docker.md)。

## 注意

1. Free trail 的 API token 有所限制，如果想要更快的速度，可以考虑付费方案
2. 欢迎提交 PR

# 感谢

- @[yetone](https://github.com/yetone)

# 贡献

- 任何 issue PR 都欢迎
- Issue 中有些 TODO 没做的都可以选
- 提交代码前请先执行 `black make_book.py` [^black]

# 其它推荐项目

- 书译 BookTranslator -> [Book Translator](https://www.booktranslator.app)

## 赞赏

谢谢就够了

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)

[^token]: https://platform.openai.com/account/api-keys
[^black]: https://github.com/psf/black
