# 设计稿：记录每次请求的目标 model

状态：**已实现** · 2026-08-25 · `proxy/stats_store.py` schema 加一列 + `server.py` 一处调用点

## 背景

`requests` 表没有 `model` 列——每行只知道 token 数，不知道这次请求打的是哪个上游
模型。生产接入方（小睦 QQ bot）想用这份数据算真实费用（不同模型定价不同，
比如 DeepSeek 的 flash/pro 差 3 倍），只能假设"全部按同一档定价"，造成约
10-15% 的估算误差（真实场景里混了少量 pro 调用）——这是接入方在生产环境里
实测踩到的具体缺口，不是臆想的功能。

## 设计

`requests` 表加一列：

```sql
ALTER TABLE requests ADD COLUMN model TEXT
```

沿用 `stats-persistence.md` 定下的迁移原则（"表结构第一版就定型，加列是
`ALTER TABLE ADD COLUMN` 一条语句的事，要时再加"）——`StatsStore.__init__`
里建表后跑一次 `PRAGMA table_info` 检查列是否已存在，不存在才 `ALTER TABLE`，
对已有 `stats.db` 文件零迁移成本、旧行的 `model` 自然是 NULL。

`record()` 加一个可选参数 `model: str | None = None`，写入这一列。`server.py`
唯一的调用点（`/v1/chat/completions` handler）从已经解析好的请求体
`body.get("model")` 取值传入——这个字段本来就在，只是没被读取利用。

## 变更范围

- `proxy/stats_store.py`：schema迁移 + `record()` 签名
- `proxy/server.py`：`stats.record(messages, compressed, model=body.get("model"))`
- `tests/test_stats_store.py`：新增迁移测试（老库无`model`列时能正常升级）+
  `record()`写入`model`的测试
- `CHANGELOG.md` + `ROADMAP.md`

## 不做的事

- 不在 `/stats` JSON 输出里加按模型聚合的新字段——现在唯一的真实消费者
  （接入方自己的成本分析脚本）是直接查 SQLite 文件，不经过 `/stats` 端点，
  没有真实需求就不加。以后要加也是独立的、可以拆开评审的小改动
- 不做旧行的 `model` 回填——历史数据本来就无法确定用的是哪个模型，留 NULL
  比编一个猜测值诚实

## 验证

1. 新库：起代理，打几条请求，`stats.db` 里 `model` 列有值
2. 旧库：拿一份没有 `model` 列的历史 `stats.db`，启动代理，确认自动迁移成功、
   不丢数据、新写入的行 `model` 有值、旧行 `model` 是 NULL
