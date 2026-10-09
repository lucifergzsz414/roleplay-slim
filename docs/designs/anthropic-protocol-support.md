# 设计稿：原生支持 Anthropic 协议（`/v1/messages`）

状态：已实现 · 2026-09-03

## 实现记录（2026-09-03）

范围严格按"验证"一节的结论收窄——只实现了 `trim_old_tool_results` 这一个
策略，没有搬运 `strip_stage_directions`/`_summarize_old_turns`：

- 新文件 `src/roleplay_slim/anthropic_proxy.py`——独立模块，不改
  `strategies.py`/`segmenter.py`（`split_into_turns` 因为只读 `role` 字段，
  原样复用，未修改）
- `ProxyConfig` 新增 `anthropic_upstream_base_url`（留空=路由 404，不猜
  路径）+ `anthropic_keep_recent_turns`（默认6，跟 OpenAI 侧的
  `keep_recent_turns` 是两个独立配置，不共用）
- `proxy/server.py` 新增 `POST /v1/messages`，注册在
  `/v1/chat/completions` 之后、`/v1/{path:path}` 兜底之前
- usage 字段映射（`_map_anthropic_usage`）在写代码前用 WebFetch 核实了
  Anthropic 官方文档的真实字段语义（`input_tokens` 只是"最后一个缓存断点
  之后"的部分，不是总数；`total_input = cache_read + cache_creation +
  input_tokens` 是文档给出的原话），不是凭训练记忆硬编的
- 流式 usage 状态机：`message_start` 给基线，`message_delta` 累加更新
  （官方文档明确"message_delta 的 usage 是累计值"），两处字段 merge
  而不是互相覆盖
- 踩了一个坑：最初想复用 `stats.record()`，用假消息
  `{"content": "\0"*before_chars}` 塞字符数进去——错的，`record()` 内部
  会拿这段内容重新跑 tiktoken 估算，得到的不是真实字符数。改成给
  `StatsStore` 新增 `record_raw()`，直接记录调用方自己算好的数字，不做
  二次估算
- 测试：`tests/test_anthropic_proxy.py`（9个单测，压缩逻辑本身）+
  `tests/test_proxy.py` 追加10个路由级测试（404兜底、正确的上游路径+
  请求头、客户端凭据优先、`system`字段透传不动、旧`tool_result`裁剪、
  代理鉴权、非流式/流式 usage 记录、流式字节转发不受影响）
- 路由、压缩、鉴权、usage 映射和流式透传均有独立测试覆盖

## 背景

roleplay-slim 最初只接受 OpenAI 线格式（`POST /v1/chat/completions`）。
使用 Anthropic Messages API 的客户端无法直接接入，必须在客户端侧切换协议，
或者完全绕过压缩代理。原生支持 `/v1/messages` 后，两类客户端都能使用相同的
本地入口，而不需要针对具体应用修改 transport 设置。

**这不是"协议转换"**：不做 Anthropic↔OpenAI 之间的格式翻译（那是
`ROADMAP.md` 明确排除的范围，会把这个项目拖进"通用网关"的坑）。这里说的
是**并行原生支持**——客户端发 Anthropic 格式，原样转发给 Anthropic 格式的
上游；客户端发 OpenAI 格式，原样转发给 OpenAI 兼容的上游。两条路径互不
干扰，没有跨格式的语义映射。

## 两种格式的实际差异（读 `segmenter.py`/`proxy/server.py` 现状后核实）

| | OpenAI (`/v1/chat/completions`) | Anthropic (`/v1/messages`) |
|---|---|---|
| system 提示词 | `messages` 数组开头若干条 `role: "system"` | 请求体顶层独立的 `system` 字段（string 或 block 数组），完全不在 `messages` 里 |
| `content` 类型 | 通常是字符串，vision 场景可以是 block 数组（`strategies.py` 的 `content_key()` 已经处理这个分支） | **始终**是 block 数组（`text`/`tool_use`/`tool_result`/`image`），没有纯字符串这条路 |
| 鉴权 | `Authorization: Bearer <key>` | `x-api-key: <key>` + 必须带 `anthropic-version` 请求头 |
| 流式格式 | `data: {...}` 每行一个完整 JSON，`usage` 在最后一个 chunk | 命名事件流：`event: message_start` / `content_block_delta` / `message_delta` / `message_stop`，`usage` **拆在两处**（`message_start.message.usage.input_tokens` 起始值，`message_delta.usage.output_tokens` 结束时累加） |
| 上游路径 | 上游根 + `/chat/completions` | 上游根 + `/messages`，且很多 OpenAI 兼容网关（包括 DeepSeek）把 Anthropic 兼容端点放在**另一个路径前缀**下（`api.deepseek.com/anthropic/v1/messages`，不是 `api.deepseek.com/v1/messages`） |

## 需要改的地方

1. **新增路由** `POST /v1/messages`，跟现有 `/v1/chat/completions` 并列
   注册（互不覆盖，各自处理自己的请求体形状）。
2. **`segmenter.py` 需要一个 Anthropic 变体**（或者给 `segment()` 加一个
   "prefix 永远是 0，由调用方在外部单独处理 system 字段"的模式）——
   Anthropic 的 `system` 字段本身就是 100% 前缀，不需要再跑
   `detect_prefix_length` 那套"扫描开头 system 消息"的逻辑；`messages`
   数组本身只有 `user`/`assistant` 两种角色，`split_into_turns` 的
   "遇到 user 就切一个新 turn"逻辑可以直接复用，但要确认 `Turn` 和各个
   `strategies.py` 函数（尤其 `dedupe_verbatim_tail`，目前专门找
   `role in ("system", "developer")`）在没有 system 消息夹在对话中间的
   情况下行为是否还有意义——Anthropic 格式里"重复的 footer 提示"这种
   模式如果存在，会以 `user` 消息里追加的 block 形式出现，不是独立的
   system 消息，`dedupe_verbatim_tail` 现在的角色判断逻辑覆盖不到这种
   形状，需要单独设计或者明确判定"不覆盖"。
3. **`_build_upstream_headers` 按协议分支**——Anthropic 请求需要设
   `x-api-key`（不是 `Authorization`）和 `anthropic-version`，且如果
   客户端自己带了 `x-api-key` 头，同样要走"客户端凭据优先"的透传逻辑
   （跟现在 `incoming_auth` 的处理方式对称）。
4. **新的流式 usage 提取器**——`_try_parse_sse_usage` 假设的是 OpenAI
   单行 JSON 形状，Anthropic 是命名事件+两处拆分的 usage，需要一个新的
   状态机（累计 `message_start` 的 `input_tokens`，看到 `message_delta`
   再补 `output_tokens`），不能改现有函数（会破坏 OpenAI 路径），只能
   新增一个平行函数。
5. **配置新增字段**——`ProxyConfig` 目前只有一个 `upstream_base_url`，
   两种协议如果指向同一个上游服务商，路径前缀通常不同（见上表），需要
   新增类似 `anthropic_upstream_base_url` 的字段（留空时该路由直接 404
   或明确报错，而不是复用 OpenAI 那个 base_url 拼出一个错误路径）。

## 明确不做的事

- 不做任何"客户端发 A 格式，代理内部转换成 B 格式再发给上游"的翻译逻辑。
  两条路由各自独立、各自透传，共享的只有压缩策略里跟格式无关的那部分
  （turn 切分的基本思路），不共享请求体本身的转换。
- 不假设两种协议的压缩效果对等——Anthropic 的 `content` 强制 block 数组
  形状，可能导致现有针对"字符串 content"调优的策略（`strip_stage_directions`
  的正则目前直接假设 `content` 是字符串）在 Anthropic 路径上完全不生效，
  需要在真正实现前用真实 Anthropic 格式的对话样本验证压缩率是否还有意义，
  而不是想当然认为"一样能省"。

## 尚未回答的问题（阻塞实现，需要先确认）

- ~~`strip_stage_directions`/`_summarize_old_turns`...要不要为 block 数组
  格式单独实现一份~~ —— 已被下方跑分回答：不需要一次性搬全部策略，
  `history_window` 的 tool_result 裁剪是主要杠杆，dedupe 是次要的，
  `strip_stage_directions`/`_summarize_old_turns` 可以先不做。
- 值不值得为了第二种协议新增字段并扩大 `ProxyConfig` 的复杂度？实现前需要
  先验证真实的压缩收益，并确保两个协议的路由、鉴权和 usage 统计彼此隔离。

## 验证：合成样本手工跑分（2026-09-03）

验证使用项目在 `test_optimizer_real_shape.py`/`benchmark-fidelity.md`
里已有的方法论：**结构忠实的合成样本**——按 Anthropic 真实格式
（`system` 独立字段、`content` 强制 block 数组、`tool_use`/`tool_result`
真实往返）手写一段多轮工具调用对话，明确标注为合成数据，不是真实流量。

脚本：`anthropic_shape_probe.py`（未入库，一次性分析脚本，不修改/依赖
`roleplay_slim` 本体）。手工实现了两个策略的 block-array 等价物：
- **dedupe 等价物**：跨整个消息列表找逐字重复的 text block（模拟这个
  环境自己真实存在的 `<system-reminder>` 重复注入现象），只保留最后一次
- **history_window 等价物**：旧轮次的 `tool_result` 内容替换成占位符
  （最近3轮原文保留）

结果（合成对话，规模不同）：

| 轮数 | 压缩前 | 只做dedupe | dedupe+window |
|---|---|---|---|
| 3 | 5682 字符 | 6.4% | 6.4%（未触发窗口） |
| 10 | 18940 字符 | 8.7% | **67.6%** |
| 30 | 56860 字符 | 9.3% | **85.0%** |

**推翻了之前的判断**：设计文档最初的"尚未回答的问题"里担心 Anthropic
路径压缩天花板低，这个假设是错的——真正的杠杆根本不在 dedupe（text block
去重只有个位数百分比，因为占比小），而在**旧轮次的 `tool_result` 内容**：
一次 `Read`/`Bash` 工具调用的原始输出一旦被推出"最近N轮"窗口，价值衰减
很快，但原样占用的字符数不衰减。对于持续复用工具调用历史的 agentic
客户端，旧工具输出正是压缩收益的主要来源。

也就是说：`history_window` 这个策略本身不需要重写，**只需要把它的
"trim 模式"从操作字符串 `content` 扩展到操作 `content` 数组里的
`tool_result` 块**，工作量比"重写全部策略"小得多——这回答了文档前面
"缩小版策略集"那个开放问题：**answer 是缩小版够用，且优先级明确
（history_window 优先于 dedupe）**，不需要一次性搬全部5个策略过去。

## 结论

技术上可行，且不违反"不做协议转换"的既有边界——这是两条并行的原生
支持路径，不是跨格式翻译。合成样本跑分证明压缩空间是真实存在的，而且
主要杠杆（`history_window` 裁剪旧 `tool_result`）范围明确、工作量可控，
不需要一次性搬全部策略过去。

原生支持的价值在于让 Anthropic 客户端直接接入，同时保持与 OpenAI 路径
完全分离；它不是跨协议翻译，也没有扩大成通用网关。
