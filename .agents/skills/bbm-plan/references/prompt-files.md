# Prompt files: lint contract and git hygiene

Loaded from SKILL.md §1 once a candidate `prompt*` file is found **and the
user has said to use it**. Do not lint before asking.

## The contract (`book_maker/cli.py:parse_prompt_arg`)

- `.json`: an object with **only** the keys `user` (required), `system` and
  `style` (both optional). Any other key is rejected outright.
- The `user` template must contain the literal placeholder `{text}`.
  `{language}` is optional.
- `.txt` becomes the user template as-is, same `{text}` rule.
- `.md` is parsed as PromptDown (pinned at 1.1.6), and only in its **block**
  form: a `## Conversation` section whose turns open with `**User:**` on its
  own line. The older `| Role | Content |` table form parses to an empty
  conversation, and the load fails with an error naming the file and the
  block form. The repo's own `prompt_md.prompt.md` sample is written in the
  table form: a broken example, not a template to copy. Only the system (or
  developer) message and the first user turn are read, so a `.md` prompt
  cannot carry a `style`; put that in a `.json` file or in the system
  message.

```markdown
# Translation Prompt

## Developer Message

You are a professional translator. Keep the register of the original.

## Conversation

**User:**
Please translate the following text into {language}:

{text}
```

Fix or report lint problems before the paid run. The CLI would reject the
file at run start anyway, but a traceback after the user has already
approved the cost is the wrong place to learn about a missing `{text}`.

## Keep them out of git

Prompt files are the user's personal voice and often carry character names
or other personal terminology — same handling as `.env`. If the working directory is a
repo:

```bash
git check-ignore prompt.json .env
```

Add whatever comes back uncovered to `.git/info/exclude`. That file is
local-only; never edit the project's tracked `.gitignore` for this.

## Where the register goes

A style instruction belongs in the `system` message, stated once, not
repeated per paragraph in `user`. The `user` template is sent for every unit
— every word in it is paid for on every request of the book.
