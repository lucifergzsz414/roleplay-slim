# 傻瓜式上手教程（零基础也能跟着做）

这份教程假设你只有一件事：一个会调用 LLM API（比如 DeepSeek、OpenAI）的聊天应用或机器人,
想让它每次发的内容变少一点,同时不改变对话本身的样子和感觉。

## 第一步 —— 装 Python（已经装过可以跳过）

去 [python.org](https://www.python.org/downloads/) 下载,选 3.10 或更新的版本。
Windows 上安装时记得勾选"Add python.exe to PATH"。

装完打开终端（Windows 用 PowerShell,Mac/Linux 用 Terminal）,输入：

```bash
python --version
```

看到类似 `Python 3.12.0` 就说明装好了。

## 第二步 —— 装 roleplay-slim

还是刚才那个终端窗口,输入：

```bash
pip install "roleplay-slim[proxy]"
```

`[proxy]` 这部分会顺带装上把它当独立服务器跑所需要的小组件（大多数人需要这个）。
如果你只打算在自己的 Python 代码里 `import` 用,不需要独立服务,可以去掉 `[proxy]`,
直接 `pip install roleplay-slim`。

## 第三步 —— 准备好你真实的 API key

用你现在给 DeepSeek、OpenAI 或其他兼容 OpenAI 接口的服务用的那个 key 就行。
把它设成一个环境变量,这样 roleplay-slim 能读到,而不需要你把它明文写进配置文件：

**Windows (PowerShell)：**
```powershell
$env:UPSTREAM_API_KEY = "sk-你的真实key"
```

**Mac/Linux：**
```bash
export UPSTREAM_API_KEY="sk-你的真实key"
```

## 第四步 —— 建一个配置文件

新建一个叫 `my_config.toml` 的文件,内容如下（如果你用的不是 DeepSeek,改一下 `upstream_base_url`）：

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

不需要看懂每一行是什么意思——这套默认值对大多数聊天应用都能直接用。
唯一值得知道的一个设置是 `keep_recent_turns`：控制最近几轮对话原封不动地全文发送。

## 第五步 —— 启动

```bash
roleplay-slim-proxy --config my_config.toml
```

看到这行就说明起来了：

```
INFO:     Uvicorn running on http://127.0.0.1:8791 (Press CTRL+C to quit)
```

这个终端窗口先别关——它现在是一个跑在你电脑上的小服务器。

## 第六步 —— 让你的应用连过去

打开你原来那个应用/机器人的设置,找到 API 的"base URL"（有些地方叫 `api_base`、
`base_url` 之类),把它从原本供应商的地址改成：

```
http://127.0.0.1:8791/v1
```

其他所有东西都不用变——你应用那边的 API key 处理方式、请求格式全都照旧。
roleplay-slim 夹在中间,把对话历史压缩一点,其他内容原样转发。

## 第七步 —— 看它实际生效

正常用你的应用。回到第五步那个终端窗口,每来一条请求就会打印一行：

```
[roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)
```

这就是压缩在生效——不用你自己猜效果好不好,数字直接摆在那。

你也可以随时用浏览器打开 `http://127.0.0.1:8791/stats`,看从启动到现在的累计统计。

## 遇到问题了？

- **"command not found: roleplay-slim-proxy"** —— 第二步没装完,或者你终端的 PATH
  没包含 pip 装东西的那个目录。可以改用 `python -m roleplay_slim.proxy --config my_config.toml`
  启动,效果一样,只是换了个调用方式。
- **应用报权限/认证错误** —— 检查一下第三步设的 `UPSTREAM_API_KEY` 和第五步启动服务
  是不是在**同一个终端窗口**里做的（环境变量不会自动带到另一个新开的终端窗口,
  换个窗口就得重新设一遍）。
- **回复效果变差了** —— 如果你开了 `enable_strip_stage_directions`,关掉它（默认就是关的）；
  如果把 `keep_recent_turns` 调低了,调回来大一点。每个设置具体是干什么的,
  看主 [README](README.md)。
