# roleplay-slim 手机版（Termux）

给手机上的 AI 聊天软件用 —— 酒馆（SillyTavern）、OpenWebUI，或者任何能填
「API 地址」的 app。

和桌面版做的是同一件事：**角色设定一个字不改，最近的对话完整保留，只整理
越来越长的旧聊天记录**，省下每次都要重复发送的那部分内容。

## 装

手机先装 [Termux](https://f-droid.org/packages/com.termux/)（建议从 F-Droid
装，Play 商店那个版本已经很久没更新了），然后：

```bash
pkg install -y curl
curl -fsSL -o roleplay-slim.sh <这个文件的地址>
bash roleplay-slim.sh
```

第一次运行会自动装好 Python 和 roleplay-slim 本体，然后直接启动。

## 用

```bash
bash roleplay-slim.sh            # 没装就装，装了就启动
bash roleplay-slim.sh status     # 看一眼当前数字
bash roleplay-slim.sh url        # 只打印要填进 app 的地址
bash roleplay-slim.sh stop       # 停掉
```

> 记得前面带上 `bash`。Termux 没有 `/usr/bin/env`，直接 `./roleplay-slim.sh`
> 会报 `bad interpreter`；加上 `bash` 就绕过了，也不用手动 chmod。

启动后屏幕上会显示一个地址，长这样：

```
    http://127.0.0.1:8796/v1
```

把它填进酒馆（Settings → API）或者别的 app 的「API 地址 / Base URL」栏，
**API Key 还是填你自己的**，然后正常聊天。数字会自己刷新：

```
  399  →  289    少发 28%
  ████████████████████░░░░░░░░
  █ 原本要发的    ░ 整理之后实际发的
  累计 128 次对话，一共少发 1,203,441 token
```

## 它不要你的 API Key

你的 key 还是填在酒馆 / app 里，跟以前一模一样。这个脚本从头到尾看不到、
也不会保存你的 key —— 代理会把 app 自己带的凭据原样转发给上游。

数据只存在 `~/.roleplay-slim/` 里（一个配置文件 + 一个统计数据库），
整个删掉就等于没装过。

## 换服务商

默认连 DeepSeek。换别的就编辑 `~/.roleplay-slim/config.toml` 里的
`upstream_base_url` 那一行，比如：

```toml
upstream_base_url = "https://api.openai.com/v1"
```

## 几个可能遇到的问题

**后台老是断** —— Android 会冻结后台进程。脚本启动时会申请唤醒锁；如果还是
断，去 Termux 的通知栏里确认唤醒锁是开着的，并在系统设置里把 Termux 的
电池优化关掉。

**端口被占用** —— 换一个：`ROLEPLAY_SLIM_PORT=8797 bash roleplay-slim.sh`

**想换数据目录** —— `ROLEPLAY_SLIM_HOME=/sdcard/rps bash roleplay-slim.sh`

**统计一直是空的** —— 说明还没有请求经过它。确认 app 里的 API 地址确实改成了
脚本显示的那个，然后随便发一条消息。

## 和桌面版的区别

桌面版是带界面的 `.exe`（Windows），这个是纯脚本。功能一样：同一个代理、
同一套压缩策略、同一个 `/stats`。手机版额外处理了 Android 特有的两件事 ——
唤醒锁，以及删掉 `pkg install` 之外的一切系统依赖。
