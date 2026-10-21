# DSH 兼容性验证

2026-10-20，产品基线 `65bfe5d`。这不是“全面适配 DSH”的声明。

## 已验证的范围

本机已安装的 `@deepseek-ai/dsh-llm-pi-ai` 和 `@deepseek-ai/dsh-llm`
版本均为 `0.1.1-rc.2`，Node.js `v24.15.0`。

测试走真实 DSH LLM runtime 和 pi-ai 适配器，经回环 HTTP 进入真实
roleplay-slim 代理；上游由 `httpx.MockTransport` 返回固定 SSE，不连接模型服务。

覆盖中文流式回复、工具调用解析、模拟工具结果回传后的续答，检查人设 system
前缀、当前问题、工具 schema、调用 ID 和结果关联保持完整，并确认旧历史发生压缩。
没有加载 DSH 用户 profile、凭据服务、真实会话、IM 插件或工具执行器。

## 复现

使用已安装开发依赖的 Python 环境和可用的 Node.js。设置 `DSH_NODE_MODULES`
为包含 `@deepseek-ai/cordis`、`@deepseek-ai/dsh-llm` 和
`@deepseek-ai/dsh-llm-pi-ai` 的现有 node_modules 目录，然后运行：

```powershell
python -m pytest tests/test_dsh_compatibility.py -q -p no:cacheprovider
```

未指定路径时测试明确跳过，不把缺少 DSH 的环境报告为通过。该测试不安装或升级
DSH，不读取 API key；子进程只使用固定的非秘密测试 key。测试 HTTP 监听随机
回环端口，退出时关闭。

## 接入方向与限制

适配入口是 DSH 的自定义模型接口，而不是重新实现 QQ、微信接入。
对于支持该配置的版本，OpenAI Chat Completions 路由应明确配置
`supportsDeveloperRole: false`、`maxTokensField: max_tokens`、
`thinkingFormat: deepseek`，避免本地代理地址使 SDK 的自动识别发生变化。
具体字段以所用 DSH 版本为准；本次没有写入用户配置。

尚未验证：新版 DSH、DeepSeek 账号登录模式、真实模型思考流、多模态、取消和异常
恢复、真实 QQ/微信收发、长期记忆保留质量，以及实际账单与缓存命中。
工具测试仅证明协议完整，不代表审批、权限或工具执行通过。

DSH 自带上下文压缩。本测试没有运行其 agent loop 或 compaction，不能证明双重
压缩的效果。默认 trim 会缩短旧消息，不能宣传为无损记忆。真实长对话实验需要
分别对照原生 DSH 与 DSH 加代理，并比较事实保留、费用和缓存，而不是只看估算
token 数。

参考：[官方模型配置](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md)、
[官方压缩模块](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/README.md)。

## 2026-10-21 源码修复进展

旧版默认 trim 的真实模型对照发现中间事实丢失。当前源码默认使用 preserve，
保留完整历史；协议探针分别验证 preserve 和明确开启的 trim，并检查前者保留
中间句、后者确实有损。真实模型事实复测见 [同日记录](dsh-live-fidelity-20261021.md)。
这两种验证分别覆盖协议和模型回忆，不等于 DSH 原生 compaction 加 IM 的完整实测。
默认 preserve 不保证有压缩收益，也不自动迁移旧配置或更新已发布二进制。
