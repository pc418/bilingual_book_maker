# bilingual_book_maker

bilingual_book_maker 是一个 AI 翻译工具，使用 ChatGPT 帮助用户制作多语言版本的 epub/txt/md/srt 文件和图书。该工具仅适用于翻译进入公共版权领域的 epub/txt 图书，不适用于有版权的书籍。请在使用之前阅读项目的 **[免责声明](./disclaimer.md)**。

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

## 准备

1. ChatGPT or OpenAI token [^token]
2. epub/txt/md books
3. 能正常联网的环境或 proxy
4. Python 3.10+

## 快速开始

本地放了一个 `test_books/animal_farm.epub` 给大家测试，`--test` 只翻开头几段。

```shell
pip install -r requirements.txt      # 或：pip install -U bbook_maker
```

最省事的办法是 provider 文件：复制示例，填上你的接口，导出一次 key，之后每条命令
只写 provider 的名字。

```shell
cp bbm_providers.example.json bbm_providers.json
# 按你的接口改 base_url、default_models、env_key
export OPENAI_API_KEY=sk-...
python3 make_book.py --book_name test_books/animal_farm.epub --provider openai --test
```

也可以直接写参数：

```shell
python3 make_book.py --book_name test_books/animal_farm.epub \
  --key sk-... --model gpt-5.6-luna --test
```

想用 ChatGPT 订阅额度而不是 API 余额：装上
[Codex CLI](https://developers.openai.com/codex/cli)，执行一次 `codex login`，
然后传 `--model codex`。不需要 key，也不需要接口地址。这条路线上整个 thread 就是
上下文，所以 `--use_context` 没有作用；不写 `--plan-classify` 就是 tag 模式。

```shell
python3 make_book.py --book_name test_books/animal_farm.epub --model codex --test
```

## 翻译服务

只要接口说的是 OpenAI 或 Anthropic 的 API，就能用。用三个参数指定它：`--api_base`
（用官方地址时可以省略）、`--key`，以及接口自己使用的模型 ID `--model`，比如默认的
`gpt-5.6-luna`、`claude-sonnet-4-6`、`deepseek-chat`。`--api_format` 用来选机器翻译引擎
（`google`、`caiyun`、`deepl`、`deeplfree`、`tencent`、`customapi`）和 `codex`；
`--provider` 按名字使用 `bbm_providers.json` 里保存的接口。旧的预设名和 key 参数
（`--model gpt4o`、`--model gemini`、`--openai_key`……）仍然可用，运行时会被改写并
打印一行说明，见[从旧参数迁移](#从旧参数迁移)。

- **任何 OpenAI 兼容接口**只需要三样东西：`--api_base`（以 `/v1` 结尾的地址）、
  `--key`，以及按接口拼写的 `--model`。用 OpenAI 官方地址时省略 `--api_base`，
  用默认模型 `gpt-5.6-luna` 时省略 `--model`。provider 条目保存的就是这三样。
- `--key` 可以写多个 key，英文逗号分隔，轮换使用，用来绕开单 key 的速率限制。
  不写这个参数时，依次读取 `$BBM_API_KEY` 和该格式自己的变量（`$OPENAI_API_KEY`、
  `$ANTHROPIC_API_KEY`），所以 key 不必出现在命令行上。
- `--model_list` 写多个模型，英文逗号分隔，轮流使用。
- `--use_context` 把前文和每一段一起发给模型，让人名和语气保持一致。选哪种模式见
  下面该参数的条目。

* DeepL

  使用 DeepL 封装的 api 进行翻译，需要付费。[DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) 来获得 token

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model deepl --deepl_key ${deepl_key}
  ```

* DeepL free

  使用 DeepL free

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deeplfree
  ```

* Claude

  `claude-*` 的模型 ID 会自动选中 anthropic 格式。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model claude-sonnet-4-6 --key ${claude_key}
  ```

* 谷歌翻译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google
  ```

* 彩云小译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format caiyun --key ${caiyun_key}
  ```

* Gemini

  通过 Gemini 的 OpenAI 兼容接口使用，模型 ID 任选。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://generativelanguage.googleapis.com/v1beta/openai/ --key ${gemini_key} --model gemini-flash-latest
  ```

* Qwen

  [Qwen-MT](https://www.aliyun.com/product/dashscope) 翻译模型：`qwen-mt-turbo` 更快更便宜，`qwen-mt-plus` 质量更高。两者都走百炼的 OpenAI 兼容接口。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://dashscope.aliyuncs.com/compatible-mode/v1 --key ${qwen_key} --model qwen-mt-turbo --language "Simplified Chinese"
  ```

* 腾讯交互翻译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format tencent
  ```

* [xAI](https://x.ai)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.x.ai/v1 --key ${xai_key} --model grok-beta
  ```

* [OrcaRouter](https://www.orcarouter.ai)

  支持 [OrcaRouter](https://www.orcarouter.ai) 网关，默认使用 `orcarouter/auto` 智能路由模型。
  它在同一端点上为 AI 代理提供网关级的零信任安全——默认拒绝地筛查每个 prompt/response
  并管控每个工具调用，无需改任何应用代码。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model orcarouter --key ${orcarouter_key}
  ```

* [Ollama](https://github.com/ollama/ollama)

  Ollama 是本机上的一个 OpenAI 兼容接口，本机地址不需要 key。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base http://localhost:11434/v1 --model ${ollama_model_name}
  ```

* [Groq](https://console.groq.com/keys)

  GroqCloud 当前支持的模型可以查看[Supported Models](https://console.groq.com/docs/models)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.groq.com/openai/v1 --key [your_key] --model llama3-8b-8192
  ```

* [Codex](https://developers.openai.com/codex/cli)

  使用 ChatGPT/Codex 套餐额度翻译，而不是 API credits。需要安装
  [Codex CLI](https://developers.openai.com/codex/cli) 并执行一次 `codex login`——会话由
  `codex app-server` 侧车进程管理，因此不需要 key。`--model codex` 跑的是
  `gpt-5.6-luna`，要换模型写 `--api_format codex --model <id>`。整本书只开一个 thread 并复用，到达 `--context-compact-at` 时压缩；
  侧车运行在沙箱中，shell、MCP 服务器、浏览和 hooks 全部关闭。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model codex --language zh-hans
  ```

* 自定义 API Provider

  常用的接口适合写进 provider 文件，而不是每次敲在命令行上。任何 OpenAI 兼容或 Anthropic 的 API（DeepSeek、SiliconFlow、本地代理）都可以作为一个条目。

  在当前目录创建 `bbm_providers.json`（全局配置放在 `~/.bbm/providers.json`）：

  ```json
  {
    "providers": {
      "deepseek": {
        "api_style": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
        "env_key": "BBM_DEEPSEEK_API_KEY"
      },
      "siliconflow": {
        "api_style": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_models": ["Qwen/Qwen2.5-72B-Instruct"],
        "env_key": "BBM_SILICONFLOW_API_KEY"
      }
    }
  }
  ```

  配置字段说明：

  | 字段 | 必填 | 说明 |
  |------|------|------|
  | `api_style` | 是 | 翻译器接口风格。支持：`openai`、`claude`、`gemini`、`qwen` |
  | `base_url` | 否 | API 地址。不填则使用该 api_style 的默认地址 |
  | `default_models` | 否 | 默认模型列表。不填则必须通过 `--model_list` 指定 |
  | `env_key` | 否 | 读取 API key 的环境变量名。不填则必须通过 `--api_key` 传入 |

  优先级：项目级 `./bbm_providers.json` 覆盖全局 `~/.bbm/providers.json`。

  `--model` 指定该 provider 下的模型；不写就用 `default_models` 的第一个。

  ```shell
  python3 make_book.py --provider deepseek --api_key sk-xxx --book_name test_books/animal_farm.epub

  export BBM_DEEPSEEK_API_KEY=sk-xxx
  python3 make_book.py --provider deepseek --book_name test_books/animal_farm.epub

  python3 make_book.py --provider deepseek --api_key sk-xxx --model_list deepseek-reasoner --book_name test_books/animal_farm.epub
  ```

## 从旧参数迁移

为旧版 CLI 写的命令仍然可用。每个被移除的参数会在运行前改写成新参数，并打印一行
说明它变成了什么：

```
$ bbook_maker --book_name book.epub --model gpt4omini --openai_key sk-...
deprecated: --openai_key is now --key
deprecated: --model gpt4omini is now --model gpt-4o-mini
```

| 旧写法 | 改写为 |
|---|---|
| `--model gpt4o` / `gpt4omini` / `o3mini` | `--model` 加对应的模型 ID |
| `--model chatgptapi` / `openai` | 直接去掉：openai 格式本来就是默认，要指定模型时用 `--model` |
| `--model openai --model_list X` | `--model_list X` |
| `--model claude` | `--model claude-haiku-4-5-20251001` |
| 具体的 `claude-*` ID | 不变，anthropic 格式由 ID 推断 |
| `--model gemini` / `geminipro` | `--api_base https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-flash-latest` / `gemini-pro-latest` |
| `--model groq --model_list X` | `--api_base https://api.groq.com/openai/v1 --model_list X` |
| `--model xai` | `--api_base https://api.x.ai/v1 --model grok-beta` |
| `--model qwen` / `qwen-mt-turbo` / `qwen-mt-plus` | `--api_base https://dashscope.aliyuncs.com/compatible-mode/v1 --model qwen-mt-*` |
| `--model google` / `caiyun` / `deepl` / `deeplfree` / `tencentransmart` | `--api_format google` / `caiyun` / `deepl` / `deeplfree` / `tencent` |
| `--custom_api URL` | `--api_format customapi --api_base URL` |
| `--openai_key` / `--claude_key` / `--gemini_key` / `--groq_key` / `--xai_key` / `--qwen_key` / `--caiyun_key` / `--deepl_key` / `--orcarouter_key` | `--key`（`--api_key` 是同一个参数，从未改名） |
| `--ollama_model M` | `--api_base http://localhost:11434/v1 --model M` |
| `--deployment_id D` | `--model D`，并把 `--api_base` 改写为该部署的 `/openai/v1` 路径 |
| `--interval` | 已删除，它只对已移除的 gemini 路由有效 |

说明：

- 旧的 key 环境变量对相应路由依然有效：改写后的 `--model groq` 仍读取
  `BBM_GROQ_API_KEY`，`--model gemini` 仍读取 `BBM_GOOGLE_GEMINI_KEY`。
- 你自己写的参数优先，所以 `--model gemini --api_base https://my-gateway/v1`
  会保留你的网关地址。
- 改写后的命令跑的还是原来那个模型，取自旧的预设列表，不会换成更新的。其中一些
  模型现已下线，接口的模型校验会直接指出。
- 不是旧别名的 `--model` 值原样传递，它就是模型 ID，这也是现在的常态。
- 指向已下线 OpenAI 模型的别名（`gpt4`、`gpt5mini`、`o1`、`o1mini`、`o1preview`）
  已移除。它们现在作为模型 ID 原样传给接口，由接口点名拒绝，同样的失败提早一步，
  报错更清楚。

## 使用说明

- 翻译完会生成一本 `{book_name}_bilingual.epub` 的双语书
- 如果出现了错误或使用 `CTRL+C` 中断命令，不想接下来继续翻译了，会生成一本 `{book_name}_bilingual_temp.epub` 的书，直接改成你想要的名字就可以了

## 参数说明

- `--model`:

  接口所用的模型 ID（`gpt-5.6-luna`、`claude-sonnet-4-6`、`deepseek-chat`）。openai 格式下默认 `gpt-5.6-luna`。有两个值表示路线而不是模型：`codex`（通过 Codex CLI 使用 ChatGPT 套餐）和 `orcarouter`（OrcaRouter 网关，key 读取 `BBM_ORCAROUTER_API_KEY`）。旧的预设值仍然可以写，会被改写成真实模型 ID 并打印说明，对照表见[从旧参数迁移](#从旧参数迁移)。其他任何接口：`--api_base <url> --key <key> --model <id>`，或一条 `--provider` 配置（见「自定义 API Provider」章节）。

- `--key`:

  接口的 API key。多个 key 用英文逗号分隔会轮换使用，绕开单 key 限流。不写时依次读取 `$BBM_API_KEY`，再读取该格式自己的变量：`$OPENAI_API_KEY`、`$ANTHROPIC_API_KEY`、`$BBM_CAIYUN_API_KEY`、`$BBM_DEEPL_API_KEY`。旧的各家 key 参数（`--openai_key` 等）仍然可用。`--api_key` 是同一个参数的旧名字。

- `--api_format`:

  接口说的 API：`openai`（默认）、`anthropic`、`codex`，或机器翻译引擎 `google`、`caiyun`、`deepl`、`deeplfree`、`tencent`、`customapi`。省略时自动推断：`anthropic.com` 的地址，或没写 `--api_base` 且模型 ID 含 `claude`，视为 `anthropic`；其余为 `openai`。推断不对或要选引擎时才需要写。

- `--source_lang`: 源语言，只有需要显式声明的接口才用（目前只有 `--api_format customapi`），默认自动检测。

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

  - `auto`（默认）：是 epub、且端点经校验会严格应用 JSON Schema 时，按 `model` 建计划。否则，以及计划建不成时，照常翻译 `--translate-tags` 选中的标签。
  - `none`：不建计划，照常翻译 `--translate-tags` 选中的标签。
  - `all`：翻译整个分区，不做分类。（旧名 `most` 仍可用，会打印一行说明。）
  - `model`：先让一个 LLM 裁决不确定的签名（标题、正文主干和诗歌分组不会被问到），然后继续翻译。可用 `--plan-classify-model X` 指定分类用的模型——指定了就意味着此模式，且分类失败会中止而不是回退。
  - `agent`：不调用 API。写出计划 JSON，打印一段可以粘贴进 coding-agent 会话（Claude Code、Codex 等）的指引，然后**在翻译前停下**。改完 action 后重跑同一条命令即可翻译。

  - `--plan-dry-run`：打印按标签签名分组的覆盖率表格，写出 `<book>_plan.json` 后退出。不需要 API key，也不消耗额度。同时遵守 `--only_filelist` / `--exclude_filelist`。
  - `<book>_plan.json`：把某个签名的 `"action"` 改成 `"skip"` 即可在正式翻译时排除它；该文件一旦存在就不会被覆盖（想重新生成请先删除）。每行带最多 5 条真实 `samples`，不用解包 epub 也能判断。
  - `--plan-min-coverage`（默认 0.5）：如果计划覆盖的正文比例低于该阈值，计划模式会直接报错退出，而不是闷头只翻译一小部分。

  ```shell
  # 先免费预览会翻译哪些内容（不需要 key）
  python3 make_book.py --book_name my_book.epub --plan-dry-run
  # 翻译整个分区
  python3 make_book.py --book_name my_book.epub --openai_key ${key} --plan-classify all
  # 先让模型分流一遍版面装置（页眉、页码等）
  python3 make_book.py --book_name my_book.epub --openai_key ${key} --plan-classify model
  # 或交给 coding agent 判断（停下、打印指引，然后重跑）
  python3 make_book.py --book_name my_book.epub --openai_key ${key} --plan-classify agent
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

    使用`--use_context`选项时，使用`--context_paragraph_limit`设置上下文段落数限制（仅 window 模式）。

- `--use_context session`:

  `--use_context` 还可以带一个模式值，选哪个取决于端点怎么计费。**端点对缓存的
  提示 token 收更低的价时，用 `--use_context session`**，OpenAI 和 Anthropic 的官方
  端点都是这样。否则用裸写的 `--use_context`（window 模式）。session 模式维护一份
  只追加的历史，每次按缓存价重读，所以上下文可以长到整章，花费反而低于 window 模式
  发几个段落。没有缓存时，同一份历史每个请求都按原价重读。历史达到压缩预算时，模型
  写一份交接报告，用来播种下一个窗口，并追加到 `<book>_handoff.md`。看进度条上的
  `cached=`：十几个请求之后仍是 0，说明端点没有缓存，改用 window 模式。

- `--context-compact-at`:

  仅 session 模式。历史在被压缩成交接报告前可以达到的估算 token 预算。默认 `8000`，最小值 `500`。

  在 `8000` 下，整体花费约为 window 模式的 0.5–1.1 倍，但携带数倍的上下文——具体比例取决于端点对缓存输入的计价。若更在意成本，`--context-compact-at 2500` 最省钱（约 0.4–0.5 倍）。

  `--context-compact-at 0` 改为按模型的上下文窗口定预算：取窗口的 90%，`--model_list` 写了多个模型时取其中最小的窗口。openai、anthropic、codex 三条路线都支持；机器翻译引擎没有模型可问，必须给具体数字。

  问不到窗口时，各路线的做法不同。openai 路线在第一次付费请求前就停下：它的 `/v1/models` 答案不会变，而默认预算等于对那个谁也量不出的模型瞎猜。anthropic 路线（网关可能提供该 API 却不报 `max_input_tokens`）和 codex 路线（sidecar 要等一轮对话花掉 token 后才报窗口）打印一行说明，用默认预算；codex 之后会继续问，直到 sidecar 给出答案。

- `--no-context-compact`:

  仅 session 模式。跳过交接报告：历史仍在达到预算时滚动，但下一个窗口从空白开始，不继承摘要。更省钱，代价是接缝处的连续性。

- `--temperature`:

  采样温度，只对接受它的格式有效。anthropic 格式总是发送。openai 格式在温度等于
  接口默认值时不发送，模型拒绝显式温度（gpt-5.x 和 o 系列）时也不发送。codex 格式
  没有这个设置，直接忽略。如 `--temperature 0.7`。

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

- `--provider`:

  使用 `bbm_providers.json` 中定义的 provider：它的 `base_url`、`api_style`、`default_models`、`env_key` 分别填入 `--api_base`、`--api_format`、`--model` 和 key。你自己写的参数优先。详见上方「自定义 API Provider」章节。

- `--api_key`:

  与 `--key` 是同一个参数，只是旧名字。两种写法都不打印提示。

### 示范用例

**如果使用 `pip install bbook_maker`，以下命令都可以改成 `bbook_maker args`。**

```shell
# 如果你想快速测一下
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --test

# 或翻译完整本书
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --language zh-hans

# 通过 Gemini 的 OpenAI 兼容接口翻译整本书
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://generativelanguage.googleapis.com/v1beta/openai/ --key ${gemini_key} --model gemini-flash-latest

# 指定环境变量来略过 --key
export OPENAI_API_KEY=${your_api_key}

# Use the DeepL model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key} --language ja

# Use the Claude model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model claude-sonnet-4-6 --key ${claude_key} --language ja

# Use the CustomAPI model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format customapi --api_base ${custom_api} --language ja

# 使用自定义 provider（如 DeepSeek）
python3 make_book.py --book_name test_books/animal_farm.epub --provider deepseek --language ja

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# 计划模式：自动发现要翻译的内容（诗歌、列表、无 <p> 包裹的正文都能覆盖）
python3 make_book.py --book_name test_books/animal_farm.epub --plan-classify all

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
# 可以在环境变量中设置BBM_CAIYUN_API_KEY，略过--key
export BBM_CAIYUN_API_KEY=${your_api_key}
```

更加小白的示例

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --api_base 'https://xxxxx/v1'

# 有可能你不需要 python3 而是python
python make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --api_base 'https://xxxxx/v1'
```

[演示视频](https://www.bilibili.com/video/BV1XX4y1d75D/?t=0h07m08s)
[演示视频 2](https://www.bilibili.com/video/BV1T8411c7iU/)

使用 Azure OpenAI service

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'
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
