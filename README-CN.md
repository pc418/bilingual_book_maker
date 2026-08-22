# bilingual_book_maker

bilingual_book_maker 是一个 AI 翻译工具，使用 ChatGPT 帮助用户制作多语言版本的 epub/txt/md/srt 文件和图书。该工具仅适用于翻译进入公共版权领域的 epub/txt 图书，不适用于有版权的书籍。请在使用之前阅读项目的 **[免责声明](./disclaimer.md)**。

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

## 准备

1. ChatGPT or OpenAI token [^token]
2. epub/txt/md books
3. 能正常联网的环境或 proxy
4. Python 3.10+

## 快速开始

本地放了一个 `test_books/animal_farm.epub` 给大家测试

```shell
pip install -r requirements.txt
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini --test
或
pip install -U bbook_maker
bbook_maker --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini --test
```

## 翻译服务

选择的是**接口地址**，而不是模型名字。一条路由由三样东西决定：

| 参数 | 含义 |
|------|------|
| `--model` | 模型 ID，直接写你的接口所使用的名字 |
| `--api_base` | 接口地址，缺省为该格式的官方地址 |
| `--key` | API key，用英文逗号分隔多个可轮换使用 |
| `--api_format` | 接口协议格式，默认自动推断，猜错时才需要显式指定 |

`--model` 写真实模型 ID —— `gpt-5-mini`、`claude-sonnet-4-6`，或网关使用的
`openai/gpt-5-mini` 这类带前缀写法。没有预设列表，也没有别名表；厂商上新模型时
不需要改这里。（旧的别名如 `gpt4` 仍可用，会被自动改写并打印提示。）

协议格式会自动判断：显式的 `--api_format` 优先，其次看 `--api_base` 的域名，
最后看模型 ID 里是否含有 `claude` 或 `anthropic`。万一判断错了（例如网关用
OpenAI 协议提供 Claude 模型），第一次请求就会发现并自动切换到 `openai`。

`--api_base` 可以直接粘贴文档里的地址：`https://host/v1`、结尾多一个斜杠、
或者整条 `https://host/v1/chat/completions`，效果相同。

`--api_format` 可选值：`openai`（默认）、`anthropic`，以及固定的机器翻译引擎
`google`、`caiyun`、`deepl`、`deeplfree`、`tencent`、`customapi`。

凡是提供 OpenAI 兼容接口的服务都走 `openai`：OpenAI 本身、Groq、xAI、DeepSeek、
SiliconFlow、OpenRouter、阿里云百炼、Gemini 的 OpenAI 兼容端点、vLLM、
LM Studio、Ollama 等。详见[模型与语言](./docs/model_lang.md)。

- `--key` 指定 API key，多个用英文逗号分隔(xxx,xxx,xxx)，可以减少接口调用次数限制带来的错误。
  也可以设置环境变量 `BBM_API_KEY`，`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 同样有效。
- `openai` 和 `anthropic` 格式必须提供 `--model`，写接口自己的模型 ID。

* OpenAI 以及所有 OpenAI 兼容接口

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --key ${key} --model gpt-5-mini
  ```

  换一个 `--api_base` 就是换一家厂商：

  ```shell
  # Groq
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://api.groq.com/openai/v1 --key ${groq_key} --model llama-3.3-70b-versatile

  # xAI
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://api.x.ai/v1 --key ${xai_key} --model grok-4

  # Gemini（OpenAI 兼容端点）
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://generativelanguage.googleapis.com/v1beta/openai/ \
    --key ${gemini_key} --model gemini-2.5-flash

  # 通义千问（百炼兼容模式）
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://dashscope.aliyuncs.com/compatible-mode/v1 \
    --key ${qwen_key} --model qwen-mt-turbo
  ```

* Claude

  地址即可推断出 anthropic 格式，无需再写 `--api_format`。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://api.anthropic.com --key ${claude_key} --model claude-sonnet-4-6
  ```

* Ollama 等本地服务

  本地地址不需要 key。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base http://localhost:11434/v1 --model ${ollama_model_name}
  ```

* 谷歌翻译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google
  ```

* DeepL

  需要付费，见 [DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key}
  ```

* DeepL free

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deeplfree
  ```

* 彩云小译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format caiyun --key ${caiyun_key}
  ```

* 腾讯交互翻译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format tencent
  ```

* 自建翻译 API

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_format customapi --api_base https://your.host/translate
  ```

## 从旧参数迁移

以前写好的命令仍然可用。所有被移除的参数会在运行前自动改写成新的接口参数，
并打印改写结果，方便你之后更新命令：

```
$ bbook_maker --book_name book.epub --model gpt4omini --openai_key sk-...
deprecated: --openai_key is now --key
deprecated: --model gpt4omini is now --model gpt-4o-mini
```

| 旧写法 | 改写为 |
|---|---|
| `--model chatgptapi` / `gpt4` / `gpt4o` / `gpt4omini` / `gpt5mini` / `o1` / `o1mini` / `o1preview` / `o3mini` | `--model <对应模型>` |
| `--model openai --model_list X` | `--model_list X` |
| `--model claude` | `--model claude-haiku-4-5-20251001` |
| 具体的 `claude-*` ID | 保持不变，anthropic 格式由 ID 推断 |
| `--model gemini` / `geminipro` | `--api_base https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-flash-latest` / `gemini-pro-latest` |
| `--model groq --model_list X` | `--api_base https://api.groq.com/openai/v1 --model_list X` |
| `--model xai` | `--api_base https://api.x.ai/v1 --model grok-beta` |
| `--model qwen` / `qwen-mt-turbo` / `qwen-mt-plus` | `--api_base https://dashscope.aliyuncs.com/compatible-mode/v1 --model qwen-mt-*` |
| `--model google` / `caiyun` / `deepl` / `deeplfree` / `tencentransmart` | `--api_format google` / `caiyun` / `deepl` / `deeplfree` / `tencent` |
| `--custom_api URL` | `--api_format customapi --api_base URL` |
| `--openai_key` / `--claude_key` / `--gemini_key` / `--groq_key` / `--xai_key` / `--qwen_key` / `--caiyun_key` / `--deepl_key` / `--api_key` | `--key` |
| `--ollama_model M` | `--api_base http://localhost:11434/v1 --model M` |
| `--deployment_id D` | `--model D`，并把 `--api_base` 改写为该部署的 `/openai/v1` 路径 |
| `--provider NAME` | 从 `bbm_providers.json` 展开为 `--api_base` / `--api_format` / `--model` |
| `--interval` | 已删除，它只对已移除的 gemini 路由有效 |

说明：

- 旧的 key 环境变量对相应路由依然有效：改写后的 `--model groq` 仍会读取
  `BBM_GROQ_API_KEY`，`--model gemini` 仍会读取 `BBM_GOOGLE_GEMINI_KEY`。
- 你显式写的新参数优先，所以 `--model gemini --api_base https://my-gateway/v1`
  会保留你的网关地址。
- 模型 ID 取自**旧的**预设列表，保证改写后跑的还是原来那个模型，而不是悄悄换成
  更新的模型。其中部分模型现已下线，接口自身的模型校验会明确报错。
- 不属于旧别名的 `--model` 值会原样传递：它们就是模型 ID，这也是现在的常态。
- 没有任何别名会改变**实际使用的模型**。两个与真实 ID 重叠的情况：`o1` 改写后
  仍是 `o1`；`qwen-mt-turbo` / `qwen-mt-plus` 会额外补上百炼的接口地址——那是
  唯一提供这两个模型的地方——你自己写的 `--api_base` 优先。

## 使用说明

- 翻译完会生成一本 `{book_name}_bilingual.epub` 的双语书
- 如果出现了错误或使用 `CTRL+C` 中断命令，不想接下来继续翻译了，会生成一本 `{book_name}_bilingual_temp.epub` 的书，直接改成你想要的名字就可以了

## 参数说明

- `--model`、`--api_base`、`--key`、`--api_format`：

  用来决定走哪条路由。大多数命令只需要写 `--model`、`--key`，必要时加
  `--api_base`；协议格式由接口域名推断，没写地址时则看模型 ID。

  `--model_list a,b,c` 用于在多个模型之间轮换以分摊限流，同时也保留给旧命令使用。
  模型只能在其中一个参数里写，不能两个都写。

  | `--api_format` | Key | 模型 |
  |----------------|-----|------|
  | `openai`（默认） | 必填 | 必须提供 `--model` |
  | `anthropic` | 必填 | 必须提供 `--model` |
  | `google` | 不需要 | 固定引擎 |
  | `deeplfree` | 不需要 | 固定引擎 |
  | `tencent` | 不需要 | 固定引擎 |
  | `customapi` | 不需要，接口地址写在 `--api_base` | 固定引擎 |
  | `caiyun` | 必填 | 固定引擎 |
  | `deepl` | 必填 | 固定引擎 |

  key 的查找顺序：`--key`、`BBM_API_KEY`，然后是该格式的惯用环境变量
  （`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`BBM_CAIYUN_API_KEY`、
  `BBM_DEEPL_API_KEY`）。本机地址（localhost）无需 key。

- `--source_lang`：

  显式指定源语言，用于那些需要写明源语言的接口（默认 `auto` 自动检测）。

- `--test`:

  如果大家没付费可以加上这个先看看效果（有 limit 稍微有些慢）。

- `--test_num`:

  配合 `--test` 指定测试翻译的文本单元数量，默认 10。

- `--language`: 指定目标语言

  - 例如： `--language "Simplified Chinese"`，预设值为 `"Simplified Chinese"`.
  - 请阅读 helper message 来查找可用的目标语言： `python make_book.py --help`

- `--proxy`

  方便中国大陆的用户在本地测试时使用代理，传入类似 `http://127.0.0.1:7890` 的字符串

- `--resume`

  手动中断后，加入命令可以从之前中断的位置继续执行。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google --resume
  ```

- `--translate-tags`

  指定需要翻译的标签，使用逗号分隔多个标签。epub 由 html 文件组成，默认情况下，只翻译 `<p>` 中的内容。例如: `--translate-tags h1,h2,h3,p,div`

  **计划模式（`--plan-classify`，仅 epub）**：不再由你挑选标签，而是把书中每一个文本节点要么归入某个翻译单元，要么按明确的理由跳过并计入报告（隐藏内容、page-list 目录、纯符号、链接等）。如果一本书的正文并不放在 `<p>` 里——例如每行一个 `<div>` 或 `<blockquote>` 的诗歌，按默认标签会被静默漏掉——那就该用这个模式。连续的短诗行会被合并成诗节窗口（最多 `--poetry-group-size` 行，默认 8 行）一次请求翻译，让模型能看到相邻诗行的上下文。计划模式下 `--translate-tags` 会被忽略。

  取值决定由谁判断哪些标签签名值得翻译：

  - `none`（默认）：不建计划，照常翻译 `--translate-tags` 选中的标签。
  - `most`：翻译整个分区，不做分类。它不提出任何问题，因此不写计划 JSON，也会忽略已有的计划文件；它打印的账本里每个签名都记为明确的 `user` 决定，避免出现“没有人决定却被翻译”的内容。
  - `model`：先让一个 LLM 裁决**每一个**尚未决定的签名，然后继续翻译整本书。可用 `--plan-classify-model X` 指定分类用的模型——指定了就意味着此模式，且分类失败会中止而不是回退。若仍有签名没被裁决，运行会停下并把这些行交给 agent 流程，而不是按默认值直接翻译。
  - `agent`：不调用 API。写出计划 JSON，打印一段可以粘贴进 coding-agent 会话（Claude Code、Codex 等）的指引，然后**在翻译前停下**。按指引把每一行决定好之后，重跑同一条命令即可翻译。

  注意后两者在花费上的区别：`agent` 一定会停下；而 `model` 只要分类全部完成，就会在同一条命令里直接把整本书翻译完。想先小样试跑，请加 `--test --test_num 20`。

  只有 `--plan-classify`（或 `--plan-dry-run`）才会进入计划模式；进入后 `--translate-tags` 会被忽略，计划会对整本书做划分。

  - `--plan-dry-run`：打印按标签签名分组的覆盖率表格，写出 `<book>_plan.json` 后退出。不需要 API key，也不消耗额度。同时遵守 `--only_filelist` / `--exclude_filelist`。它写出的行全部处于未决状态——之后用 `model` 跑会由 LLM 裁决，用 `agent` 跑会把这些行交给 coding agent，你也可以自己改。
  - `<book>_plan.json`：一行要算“已决定”，必须同时填三个字段——`"action"`（`"translate"` 或 `"skip"`）、`"decided_by"`（自己手改就写 `"user"`）、以及说明这段文字是什么的 `"content_type"`。先命名再裁决，命名本身就是理由；没有它的判断无法复核，运行会直接拒绝。每行带最多 5 条真实 `samples`，不用解包 epub 也能判断。你已经做出的决定不会被覆盖；只有当设置变化引入了新的签名时，文件才会被重写以补上这些新行（想完全重新生成请先删除）。
  - `--plan-min-coverage`（默认 0.5）：如果计划覆盖的正文比例低于该阈值，计划模式会直接报错退出，而不是闷头只翻译一小部分。

  ```shell
  # 先免费预览会翻译哪些内容（不需要 key）
  python3 make_book.py --book_name my_book.epub --plan-dry-run
  # 翻译整个分区
  python3 make_book.py --book_name my_book.epub --key ${key} --model gpt-5-mini --plan-classify most
  # 先让模型分流一遍版面装置（页眉、页码等）
  python3 make_book.py --book_name my_book.epub --key ${key} --model gpt-5-mini --plan-classify model
  # 或交给 coding agent 判断（停下、打印指引，然后重跑）
  python3 make_book.py --book_name my_book.epub --key ${key} --model gpt-5-mini --plan-classify agent
  ```

- `--exclude-translate-tags`:

  指定不翻译其内部内容的 HTML 标签，多个标签用逗号分隔，默认 `sup,code`。
  例如 `--exclude-translate-tags code,pre`；传入空字符串
  `--exclude-translate-tags ""` 可取消默认排除。

- `--book_from`

  选项指定电子阅读器类型（现在只有 kobo 可用），并使用 `--device_path` 指定挂载点。

- `--api_base ${url}`

  如果你遇到了墙需要用 Cloudflare Workers 替换 api_base 请使用 `--api_base ${url}` 来替换。
  **请注意，此处你输入的 api 应该是'`https://xxxx/v1`'的字样，域名需要用引号包裹**

- `--allow_navigable_strings`

  如果你想要翻译电子书中的无标签字符串，可以使用 `--allow_navigable_strings` 参数，会将可遍历字符串加入翻译队列，**注意，在条件允许情况下，请寻找更规范的电子书**

- `--prompt`

  如果你想调整 prompt，你可以使用 `--prompt` 参数。有效的占位符包括 `{text}` 和 `{language}`。你可以用以下方式配置 prompt:

  - 如果您不需要设置 `system` 角色，可以这样：`--prompt "Translate {text} to {language}"` 或者 `--prompt prompt_template_sample.txt`（示例文本文件可以在 [./prompt_template_sample.txt](./prompt_template_sample.txt) 找到）。

  - 如果您需要设置 `system` 角色，可以使用以下方式配置：`--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'`，或者 `--prompt prompt_template_sample.json`（示例 JSON 文件可以在 [./prompt_template_sample.json](./prompt_template_sample.json) 找到）。

  - 你也可以用环境以下环境变量来配置 `system` 和 `user` 角色 prompt：`BBM_CHATGPTAPI_USER_MSG_TEMPLATE` 和 `BBM_CHATGPTAPI_SYS_MSG`。
  该参数可以是提示模板字符串，也可以是模板 `.txt` 文件的路径。

- `--batch_size`

  指定批量翻译的行数(默认行数为 10，目前只对 txt 生效)

- `--accumulated_num`:

  达到累计token数开始进行翻译。gpt3.5将total_token限制为4090。
  例如，如果您使用`--accumulation_num 1600`，则可能会输出2200个令牌，另外200个令牌用于系统指令（system_message）和用户指令（user_message），1600+2200+200 = 4000，所以token接近极限。你必须选择一个自己合适的值，我们无法在发送之前判断是否达到限制

- `--use_context`:

  prompts the model to create a three-paragraph summary. If it's the beginning of the translation, it will summarize the entire passage sent (the size depending on `--accumulated_num`).
  For subsequent passages, it will amend the summary to include details from the most recent passage, creating a running one-paragraph context payload of the important details of the entire translated work. This improves consistency of flow and tone throughout the translation. This option is available for all ChatGPT-compatible models and Gemini models.

  模型提示词将创建三段摘要。如果是翻译的开始，它将总结发送的整个段落（大小取决于`--accumulated_num`）。
  对于后续的段落，它将修改摘要，以包括最近段落的细节，创建一个完整的段落上下文负载，包含整个翻译作品的重要细节。 这提高了整个翻译过程中的流畅性和语气的一致性。 此选项适用于所有ChatGPT兼容型号和Gemini型号。

  - `--context_paragraph_limit`:

    使用`--use_context`选项时，使用`--context_paragraph_limit`设置上下文段落数限制。

- `--temperature`:

  使用 `--temperature` 设置 `chatgptapi`/`gpt4`/`claude`模型的temperature值.
  如 `--temperature 0.7`.

- `--block_size`:

  使用`--block_size`将多个段落合并到一个块中。这可能会提高准确性并加快处理速度，但可能会干扰原始格式。必须与`--single_translate`一起使用。
  例如：`--block_size 5 --single_translate`。

- `--single_translate`:

  使用`--single_translate`只输出翻译后的图书，不创建双语版本。

- `--translation_style`:

  为 EPUB 译文应用完整 CSS，例如
  `--translation_style "color: #808080; font-style: italic;"`。

- `--translation_color`:

  只设置 EPUB 译文颜色的快捷参数，例如 `--translation_color "#1e90ff"`。
  如果同时传入 `--translation_style`，完整样式优先。

- `--pdf_layout {none,top-bottom,side-by-side,all}`:

  为 PDF 输入选择额外生成的双语 PDF 版式。默认 `none` 不额外生成 PDF；
  `all` 会同时尝试上下对照和左右对照。双语 TXT 和 EPUB 输出不受该参数影响。

- `--sentence_mode`:

  将 EPUB 的每个段落拆成句子逐句翻译，而不是整段翻译。与 EPUB 计划模式不兼容。

- `--batch` / `--batch-use`:

  使用 ChatGPT Batch API 的两阶段 EPUB 流程。先用 `--batch` 提交任务，再以
  `--batch-use` 重跑以等待并使用结果。二者都与计划模式不兼容。

- `--parallel-workers`:

  并行处理 EPUB 章节或 Markdown 批次/分段，默认 1，建议 2–4。其他输入加载器目前
  虽然接受这个共享参数，但不会并行执行。EPUB 的 `--use_context` 在并行模式下是
  章节内上下文，而不是全书共享上下文。

- `--quiet`:

  关闭 EPUB 进度条和逐段原文/译文输出，但保留报告与错误。适合日志文件和 Agent
  非交互运行。

- `--retranslate "$translated_filepath" "file_name_in_epub" "start_str" "end_str"`:

  - 重新翻译，从 start_str 到 end_str 的标记:

  ```shell
  python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'
  ```

  - 只重新翻译包含 `start_str` 的标签时，第四个参数传入空字符串：

  ```shell
  python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' ''
  ```

- `--extra_body`:

  以 JSON 字符串向 ChatGPT/OpenAI 衍生请求路径透传额外参数，包括 OpenAI 风格的
  自定义 provider 和 xAI。Claude、Gemini、Qwen、Groq 等其他翻译器目前会忽略该参数。例如：

  ```shell
  python3 make_book.py --book_name book.epub --extra_body '{"chat_template_kwargs":{"enable_thinking":false}}'
  ```

### 示范用例

**如果使用 `pip install bbook_maker`，以下命令都可以改成 `bbook_maker args`。**

```shell
# 如果你想快速测一下
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini --test

# 或翻译完整本书
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini --language zh-hans

# Or translate the whole book using Gemini
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://generativelanguage.googleapis.com/v1beta/openai/ --key ${gemini_key} --model gemini-2.5-flash

# 指定环境变量来略过 --key
export BBM_API_KEY=${your_api_key}

# Use the DeepL model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key} --language ja

# Use the Claude model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.anthropic.com --key ${claude_key} --model claude-sonnet-4-6 --language ja

# Use the CustomAPI model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format customapi --api_base ${custom_api} --language ja

# 任意 OpenAI 兼容厂商（如 DeepSeek）
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.deepseek.com/v1 --key sk-xxx --model deepseek-chat --language ja

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# 计划模式：自动发现要翻译的内容（诗歌、列表、无 <p> 包裹的正文都能覆盖）
python3 make_book.py --book_name test_books/animal_farm.epub --plan-classify most

# 修改prompt
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# 或者
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"
# 翻译 kobo e-reader 中，來自 Rakuten Kobo 的书籍
python3 make_book.py --book_from kobo --device_path /tmp/kobo

# 翻译 txt 文件
python3 make_book.py --book_name test_books/the_little_prince.txt --test
# 聚合多行翻译 txt 文件
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20


# 使用彩云小译翻译(彩云api目前只支持: 简体中文 <-> 英文， 简体中文 <-> 日语)
# 彩云提供了测试token（3975l6lr5pcbvidl6jl2）
# 你可以参考这个教程申请自己的token (https://bobtranslate.com/service/translate/caiyun.html)
python3 make_book.py --api_format caiyun --key 3975l6lr5pcbvidl6jl2 --book_name test_books/animal_farm.epub
# 可以在环境变量中设置BBM_CAIYUN_API_KEY，略过 --key
export BBM_CAIYUN_API_KEY=${your_api_key}
```

更加小白的示例

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --model gpt-5-mini --api_base 'https://xxxxx/v1'

# 有可能你不需要 python3 而是python
python make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --model gpt-5-mini --api_base 'https://xxxxx/v1'
```

[演示视频](https://www.bilibili.com/video/BV1XX4y1d75D/?t=0h07m08s)
[演示视频 2](https://www.bilibili.com/video/BV1T8411c7iU/)

使用 Azure OpenAI service

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key XXXXX --model deployment-name --api_base 'https://example-endpoint.openai.azure.com/openai/v1'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --key XXXXX --model deployment-name --api_base 'https://example-endpoint.openai.azure.com/openai/v1'
```

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
