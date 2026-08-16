# 设计稿：/stats 持久化

状态：**已实现** · 2026-08-16 · `proxy/stats_store.py` + config `[stats]` + 持久化测试

**与设计稿的两处偏离（都是砍镀金）**：
1. **不做 convo_key 会话归属**——单一部署没人看 per-conversation 明细，SQLite 加列是
   `ALTER TABLE ADD COLUMN` 一条语句的事，要时再加。
2. **不做批量 flush**——每请求同步 INSERT 够快（单部署流量量级），且零窗口意味着崩溃
   不丢数据，比"最多丢一个 flush 窗口"更简单更对。真要批量是以后的事。

## 背景

`/stats` 现在把所有计数放在内存里，代理重启即清零。小睦这种长跑服务重启不频繁还好，但对想"挂一晚看数字"的接入方不友好，也让 `savings_pct` 的绝对值没法做长期对照。

## 目标

1. **持久化**：请求统计跨重启存活
2. **加一个维度**：per-conversation 拆解（现在只有总量）

## 设计

### 存储：SQLite，每请求一行

```
stats.db
  requests(
    id INTEGER PRIMARY KEY,
    ts        TEXT,          -- ISO 时间
    prefix_share REAL,       -- 本请求前缀占比
    tokens_before INTEGER,   -- 压缩前（本地估算）
    tokens_after  INTEGER,   -- 压缩后
    upstream_prompt INTEGER, -- 上游实际计费（provider 报告）
    convo_key TEXT,          -- 会话归属
  )
```

`/stats` 的聚合查询从这个表做（SUM/AVG/COUNT）。内存不再持有总量，只做写入缓冲。

### 会话归属（convo_key）怎么定

这是唯一需要拍板的点。三个候选：

| 方案 | 说明 | 取舍 |
|---|---|---|
| A. 客户端 `x-convo-id` 头 | 接入方显式传，最准 | 需要接入方配合，老客户端没有 |
| B. 前缀 + 首条 user 消息的哈希 | 零接入成本，纯服务端推导 | 首条 user 相同则归并到同会话，边界近似 |
| C. 只按 user 字段分组 | 零成本 | 伴侣类 bot 一人一会话，够用；群里会混 |

**建议 A+B 双轨**：有 `x-convo-id` 用它，没有就退到 B。C 不单独做（和 B 重叠）。

### 配置

`config.toml` 加一项：
```toml
[stats]
persist = true          # 默认 true；关掉回到纯内存（零磁盘写）
db_path = "stats.db"     # 相对代理工作目录
```

### 落盘策略

- 每请求同步 INSERT 太慢 → **批量 + 定时 flush**（每 30s 或每 200 条）
- 崩溃最多丢一个 flush 窗口，可接受；`/stats` 永远从磁盘读，一致性优先
- 启动时建表（IF NOT EXISTS），旧库自动复用，无迁移成本（表结构第一版就定型）

## 变更范围（若通过评审）

- 新增 `proxy/stats_store.py`（SQLite 封装 + flush 循环）
- `server.py` 的 `/stats` 和记录点改走 store
- `config.py` 加 `[stats]` 段解析
- 测试：构造请求 → 重启代理进程 → `/stats` 数字仍在（进程级集成测试，不是纯单测）

## 不做的事

- 不暴露按用户明细的 API（隐私 + 单一部署没必要）
- 不做保留期清理的默认值——量级很小（一行几百字节），先留着；真要加是加配置不是写死
- 不动 `/healthz` 和转发路径

## 验证

1. 起代理，打几条请求，`/stats` 有数字
2. 杀掉进程重启，`/stats` 数字仍在且单调增长
3. 关掉 `persist=false` 行为回退到现状（纯内存）
