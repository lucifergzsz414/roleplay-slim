# Quick Start

If you've never touched Python before, don't worry — this doesn't assume
anything beyond "I have a chat app or bot that talks to an LLM API (like
DeepSeek or OpenAI) and I want it to send less text per request."

## 1. Get Python installed

If `python --version` already works in your terminal, skip this.
Otherwise grab it from [python.org](https://www.python.org/downloads/),
3.10 or newer. On Windows, check the "Add python.exe to PATH" box during
setup or none of the later commands will work.

```bash
python --version
```

Should print something like `Python 3.12.0`.

## 2. Install the package

```bash
pip install "roleplay-slim[all]"
```

That pulls in the proxy server and the more accurate token-counting stats
in one go, so you don't have to figure out which extra you need. Total
download is under 5 MB. If you're just going to `import` this in your own
code and skip the standalone server entirely, `pip install roleplay-slim`
on its own is fine too.

## 3. Have your API key ready

Whatever key you already use for DeepSeek, OpenAI, or another compatible
provider works here. Put it in an environment variable instead of typing
it into a config file:

```powershell
# Windows PowerShell
$env:UPSTREAM_API_KEY = "sk-your-real-key"
```

```bash
# Mac/Linux
export UPSTREAM_API_KEY="sk-your-real-key"
```

## 4. Write a small config file

Save this as `my_config.toml` (swap `upstream_base_url` if you're not on
DeepSeek):

```toml
[proxy]
upstream_base_url = "https://api.deepseek.com/v1"
upstream_api_key_env = "UPSTREAM_API_KEY"
host = "127.0.0.1"
port = 8791

[compressor]
keep_recent_turns = 3
enable_strip_stage_directions = false
```

You don't need to understand every field to get started — these defaults
are sane for most chat apps. The one to actually think about is
`keep_recent_turns`: it's how many of the most recent exchanges get sent
completely untouched.

## 5. Start it up

```bash
roleplay-slim-proxy --config my_config.toml
```

Once it's running you'll see something like:

```
INFO:     Uvicorn running on http://127.0.0.1:8791 (Press CTRL+C to quit)
```

Keep that terminal open. It's a small local server now.

## 6. Point your app at it

Find wherever your app or bot sets its API base URL (sometimes called
`api_base`, sometimes `base_url`) and swap it for:

```
http://127.0.0.1:8791/v1
```

That's it — no other code changes. Your app keeps handling its API key
and request format exactly the same way; roleplay-slim just sits in the
middle, trims the conversation history a bit, and passes everything else
straight through.

## 7. Confirm it's actually doing something

Use the app like normal. Back in the terminal from step 5, you'll get a
line per request:

```
[roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)
```

That number is the proof — you don't have to take anyone's word for
whether compression is happening. `http://127.0.0.1:8791/stats` in a
browser gives you the running totals if you want them in JSON instead.

## If something's not working

**"command not found: roleplay-slim-proxy"** — install probably didn't
finish, or it landed somewhere your PATH doesn't check. Run
`python -m roleplay_slim.proxy --config my_config.toml` instead; does the
same thing.

**App can't authenticate** — make sure `UPSTREAM_API_KEY` was set in the
*same* terminal window you launched the proxy from. It doesn't carry over
to a new window automatically.

**Replies got worse** — if you turned on `enable_strip_stage_directions`,
switch it back off (it defaults to off for a reason). If you cranked
`keep_recent_turns` down low, bring it back up. Details on what each
setting actually does are in the [README](README.md).
