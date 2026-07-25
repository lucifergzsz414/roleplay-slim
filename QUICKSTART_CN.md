# 上手教程

没碰过 Python 也没关系，这篇不预设别的，只假设一件事：你有个聊天应用或机器人在调 LLM API（DeepSeek、OpenAI 之类），想让它每次发的内容少一点。

## 1. 先把 Python 装上

如果终端里敲 `python --version` 已经有反应，这步跳过。没有的话去 [python.org](https://www.python.org/downloads/) 下一个，3.10 以上都行。Windows 装的时候记得勾上"Add python.exe to PATH"，不然后面的命令都跑不起来。

```bash
python --version
```

看到类似 `Python 3.12.0` 就对了。

## 2. 装包

```bash
pip install "roleplay-slim[all]"
```

这一条把代理服务和更准的 token 统计一起装了，省得你去纠结该装哪个 extra。总共下载不到 5MB。如果你就是想在自己代码里 `import` 用一下，完全不打算跑独立服务，装个 `pip install roleplay-slim` 就够。

## 3. 把 API key 准备好

用你现在给 DeepSeek、OpenAI 或者其他兼容接口用的那个 key 就行，不用换。放进环境变量里，别写死在配置文件里：

```powershell
# Windows PowerShell
$env:UPSTREAM_API_KEY = "sk-你的真实key"
```

```bash
# Mac/Linux
export UPSTREAM_API_KEY="sk-你的真实key"
```

## 4. 写个配置文件

存成 `my_config.toml`（如果不是用 DeepSeek，改一下 `upstream_base_url`）：

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

这些字段不用全搞懂也能先跑起来，默认值对大多数聊天应用都合适。真要留意的就一个：`keep_recent_turns`，最近几轮对话会原封不动全文发出去，这个数字管的就是"几轮"。

## 5. 启动

```bash
roleplay-slim-proxy --config my_config.toml
```

跑起来之后会看到：

```
INFO:     Uvicorn running on http://127.0.0.1:8791 (Press CTRL+C to quit)
```

这个终端窗口别关，现在它就是本机上一个小服务器。

## 6. 让你的应用连过去

去你原来那个应用/机器人的设置里找 API 的 base URL（有的叫 `api_base`，有的就叫 `base_url`），换成：

```
http://127.0.0.1:8791/v1
```

其他都不用动——你那边 API key 怎么处理、请求格式长什么样，全部照旧。roleplay-slim 就是夹在中间把历史记录压一压，其他东西原样转发过去。

## 7. 确认它真的在干活

正常用你的应用。回到第 5 步开着的那个终端，每来一条请求就会打一行：

```
[roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)
```

数字摆在这，效果好不好不用猜。浏览器打开 `http://127.0.0.1:8791/stats` 能看到累计统计，JSON 格式。

## 卡住了怎么办

**"command not found: roleplay-slim-proxy"** —— 大概率是装的时候没装完，或者装到了 PATH 找不到的地方。换成 `python -m roleplay_slim.proxy --config my_config.toml` 试试，效果一样。

**应用连不上，报权限错** —— 检查一下 `UPSTREAM_API_KEY` 是不是在启动代理**那个同一个**终端窗口里设的，换个新窗口环境变量不会自动带过去。

**回复变差了** —— 如果开了 `enable_strip_stage_directions`，关掉（默认就是关的，不是没道理的）。`keep_recent_turns` 调太低的话调回去。每个设置具体干嘛的，看 [README](README.md)。
