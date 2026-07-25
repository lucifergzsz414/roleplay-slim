# Quick Start (no prior experience needed)

This guide assumes nothing except: you have a chat app or bot that talks to
an LLM API (like DeepSeek or OpenAI), and you want it to send less text per
request without changing how the conversation looks or feels.

## Step 1 — Install Python (skip if you already have it)

Download from [python.org](https://www.python.org/downloads/) — version
3.10 or newer. On Windows, tick "Add python.exe to PATH" during install.

Check it worked by opening a terminal (PowerShell on Windows, Terminal on
Mac/Linux) and typing:

```bash
python --version
```

You should see something like `Python 3.12.0`.

## Step 2 — Install roleplay-slim

In the same terminal:

```bash
pip install "roleplay-slim[all]"
```

`[all]` installs everything in one shot (the proxy server pieces, plus
accurate token-count stats) so you don't need to think about which extra
name to type — the whole thing is under 5 MB. If you only plan to
`import` this inside your own Python code and never run the standalone
proxy, you can drop down to a plain `pip install roleplay-slim` instead.

## Step 3 — Get your real API key ready

You need whatever API key you already use for DeepSeek, OpenAI, or another
OpenAI-compatible provider. Set it as an environment variable so
roleplay-slim can read it without you typing it into a config file:

**Windows (PowerShell):**
```powershell
$env:UPSTREAM_API_KEY = "sk-your-real-key-here"
```

**Mac/Linux:**
```bash
export UPSTREAM_API_KEY="sk-your-real-key-here"
```

## Step 4 — Create a config file

Make a file called `my_config.toml` with this content (adjust
`upstream_base_url` if you're not using DeepSeek):

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

Don't worry about understanding every line — these defaults work for most
chat apps out of the box. The one setting worth knowing:
`keep_recent_turns` controls how many of the most recent back-and-forth
exchanges are always sent in full, untouched.

## Step 5 — Run it

```bash
roleplay-slim-proxy --config my_config.toml
```

You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:8791 (Press CTRL+C to quit)
```

Leave this window open — it's now running as a small local server.

## Step 6 — Point your app at it

In whatever app or bot you already have, find the setting for the API's
"base URL" (sometimes called `api_base`, `base_url`, or similar) and change
it from the real provider's URL to:

```
http://127.0.0.1:8791/v1
```

Everything else about your app stays exactly the same — same API key
handling on your app's side, same request format. roleplay-slim sits in
the middle, shrinks the conversation history a bit, and forwards
everything else untouched.

## Step 7 — Watch it work

Use your app normally. Back in the terminal window from Step 5, you'll see
a line print for every request:

```
[roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)
```

That's the compression working — no reply-quality guessing needed on your
part, the number is right there.

You can also open `http://127.0.0.1:8791/stats` in a browser at any time to
see running totals across every request so far.

## Something not working?

- **"command not found: roleplay-slim-proxy"** — the install in Step 2
  didn't finish, or your terminal's PATH doesn't include where pip
  installed it. Try `python -m roleplay_slim.proxy --config my_config.toml`
  instead — same effect, different way to launch it.
- **Your app gets an authorization error** — double-check
  `UPSTREAM_API_KEY` in Step 3 is the *same terminal session* you're
  running Step 5 from (environment variables don't carry over between
  separate terminal windows unless you set them again).
- **Replies look worse than before** — turn off
  `enable_strip_stage_directions` if you turned it on (it's off by
  default), and lower `keep_recent_turns` back up if you set it low. See
  the main [README](README.md) for what each setting actually does.
