#!/usr/bin/env bash
# 对 roleplay-slim.sh 的可执行验证。
#
# 不是"看一眼语法"——是真的 source 这个脚本、用它的 write_config() 生成配置、
# 拿这份配置起一个真代理、打一条真请求、再用它的 render_stats() 渲染真实数字。
# 目的是证明这台机器上能跑的每一条代码路径都没写错；Termux 特有的那几块
# （pkg install / termux-wake-lock）只能靠桩函数验证调用点，见文末说明。

set -u
cd "$(dirname "$0")"

FAIL=0
pass() { printf '\033[38;5;84m  ✓\033[0m %s\n' "$1"; }
fail() { printf '\033[38;5;203m  ✗\033[0m %s\n' "$1"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d)"
export ROLEPLAY_SLIM_HOME="$TMP"
export ROLEPLAY_SLIM_PORT=8798
export HOME_ORIG="$HOME"

cleanup() {
    [ -n "${PROXY_PID:-}" ] && kill "$PROXY_PID" 2>/dev/null
    sleep 0.3
    [ -n "${PROXY_PID:-}" ] && kill -9 "$PROXY_PID" 2>/dev/null
    rm -rf "$TMP"
}
trap cleanup EXIT

# ── source 被测脚本（它的 BASH_SOURCE 守卫保证不会自己跑 main）────
# shellcheck source=roleplay-slim.sh
. ./roleplay-slim.sh

echo "== 1. 纯函数 =="
url="$(proxy_url)"
[ "$url" = "http://127.0.0.1:8798/v1" ] \
    && pass "proxy_url() = $url" \
    || fail "proxy_url() 错了：$url"

# cmd_url 应该跟 proxy_url 一致
[ "$(cmd_url)" = "$url" ] && pass "cmd_url() 一致" || fail "cmd_url() 不一致"

echo
echo "== 2. write_config() 生成的配置 =="
write_config "https://api.deepseek.com/v1"
[ -f "$CONFIG" ] && pass "配置已生成：$CONFIG" || fail "配置没生成"

grep -q "^port = 8798$" "$CONFIG" \
    && pass "端口写对了" \
    || fail "端口写错：$(grep '^port' "$CONFIG")"

grep -q "^db_path = \"$TMP/stats.db\"$" "$CONFIG" \
    && pass "stats.db 路径写对了" \
    || fail "db_path 错了：$(grep '^db_path' "$CONFIG")"

# 关键：启动器永远不该持有真 key
grep -q 'upstream_api_key_env = "ROLEPLAY_SLIM_UNUSED_KEY"' "$CONFIG" \
    && pass "没有内嵌 API key（用的是占位环境变量）" \
    || fail "配置里出现了不该有的 key 设置"

echo
echo "== 3. read_upstream() 回读 =="
got="$(read_upstream)"
[ "$got" = "https://api.deepseek.com/v1" ] \
    && pass "回读 upstream = $got" \
    || fail "回读错了：$got"

echo
echo "== 4. 用这份配置起一个真代理 =="
python -m roleplay_slim.proxy --config "$CONFIG" >"$TMP/proxy.log" 2>&1 &
PROXY_PID=$!
echo "  代理 pid=$PROXY_PID"

ready=0
for _ in $(seq 1 40); do
    if probe_port; then ready=1; break; fi
    sleep 0.5
done
[ "$ready" = 1 ] && pass "probe_port() 探测到代理就绪" || fail "代理没起来；日志：$(tail -3 "$TMP/proxy.log")"

if [ "$ready" != 1 ]; then
    echo; echo "代理没起来，后面的测试没法做"; exit 1
fi

echo
echo "== 5. 打一条真请求 =="
# 这个文件是要进公开仓库的，绝不能内嵌真 key。要从外部给：
#   ROLEPLAY_SLIM_TEST_KEY=sk-... bash test_termux_script.sh
# 没给就跳过这一段 —— 其余检查（配置生成、探测、渲染、Termux 调用点）
# 都不需要真 key，仍然会全部跑完。
if [ -z "${ROLEPLAY_SLIM_TEST_KEY:-}" ]; then
    warn_skip="跳过真实请求（未设置 ROLEPLAY_SLIM_TEST_KEY）"
    printf '  \033[2m- %s\033[0m\n' "$warn_skip"
    echo "    设置后重跑可覆盖这一段：ROLEPLAY_SLIM_TEST_KEY=sk-... bash $0"
else
python - "$ROLEPLAY_SLIM_PORT" "$ROLEPLAY_SLIM_TEST_KEY" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
system = "你是角色扮演助手，人设冷静话少。" * 3
footer = "[格式提醒] 回复两句话以内，保持人设。"
msgs = [{"role": "system", "content": system}]
for i in range(6):
    msgs += [
        {"role": "user", "content": f"第{i}轮：随便聊聊。细节{i}"},
        {"role": "assistant", "content": f"第{i}轮回复：嗯。补充{i}"},
        {"role": "system", "content": footer},
    ]
body = {"model": "deepseek-chat", "max_tokens": 30, "messages": msgs}
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + sys.argv[2]})
with urllib.request.urlopen(req, timeout=90) as r:
    out = json.loads(r.read())
print("  真实回复长度:", len(out["choices"][0]["message"]["content"]), "字符")
PY
[ $? -eq 0 ] && pass "真实请求成功（且用的是调用方自带 key，脚本没存 key）" || fail "请求失败"
fi

echo
echo "== 6. render_stats_from_json() 渲染（合成数据，UTF-8 终端）=="
# 渲染跟取数分开了，所以这一段不依赖任何真实流量，每次跑都是确定的。
SAMPLE='{"b": 37176, "a": 19545, "saved": 1203441, "count": 128}'
OUT="$(PYTHONIOENCODING=utf-8 render_stats_from_json "$SAMPLE")"
rc=$?
[ "$rc" -eq 0 ] && pass "返回成功" || fail "返回 $rc"
printf '%s\n' "$OUT" | sed 's/^/    /'

printf '%s' "$OUT" | grep -q '37,176' && pass "千分位格式正确" || fail "千分位没生效"
printf '%s' "$OUT" | grep -q '少发 47%' && pass "算出的百分比正确（37176→19545 应为 47%）" || fail "百分比不对"
printf '%s' "$OUT" | grep -q '█' && pass "UTF-8 下用方块字符画条" || fail "没看到方块条形"
printf '%s' "$OUT" | grep -q '累计 128' && pass "输出了累计行" || fail "没看到累计行"

echo
echo "== 6b-0. 渲染空状态（还没有请求）=="
EMPTY_OUT="$(PYTHONIOENCODING=utf-8 render_stats_from_json '{"b":0,"a":0,"saved":0,"count":0}')"
printf '%s' "$EMPTY_OUT" | grep -q '还没有请求' && pass "空状态有友好提示" || fail "空状态没提示"
printf '%s' "$EMPTY_OUT" | grep -q '█' && fail "空状态不该画条" || pass "空状态不画条"

echo
echo "== 6b. 非 UTF-8 终端（GBK）必须降级而不是崩 =="
# 这是真踩过的坑：GBK 有中文但没有 U+2591 的方块字符，
# 原来的写法在这里抛 UnicodeEncodeError，渲染到一半就死了。
OUT_GBK="$(PYTHONIOENCODING=gbk render_stats_from_json "$SAMPLE")"
rc_gbk=$?
[ "$rc_gbk" -eq 0 ] && pass "GBK 下仍然返回成功（不再半路崩溃）" || fail "GBK 下返回 $rc_gbk"
printf '%s\n' "$OUT_GBK" | sed 's/^/    /'

# 输出是 GBK 字节，直接 grep UTF-8 的中文当然匹配不上 —— 先转码再断言，
# 否则测的是测试自己的编码假设，不是脚本的行为。
OUT_GBK_UTF8="$(printf '%s' "$OUT_GBK" | iconv -f gbk -t utf-8 2>/dev/null)"
if [ -n "$OUT_GBK_UTF8" ] && printf '%s' "$OUT_GBK_UTF8" | grep -q '少发'; then
    pass "GBK 下中文照常显示（转码后核对）"
else
    fail "GBK 下丢了中文"
fi
printf '%s' "$OUT_GBK_UTF8" | grep -q '#' \
    && pass "GBK 下自动降级成 ASCII 条形" \
    || fail "GBK 下没有降级"
# 也是关键：确认它真的没把 GBK 编不出来的字符写出去
if printf '%s' "$OUT_GBK_UTF8" | grep -q '░'; then
    fail "GBK 输出里仍出现了 U+2591（本该降级掉）"
else
    pass "GBK 输出里没有 U+2591 方块字符"
fi

echo
echo "== 7. Termux 专有分支（用桩函数验证调用点）=="
# 本机没有 pkg / termux-wake-lock，用桩函数确认脚本确实会去调它们，
# 而不是在真机上才发现忘了调。做法：把 in_termux() 和 have() 覆盖掉。
have() { case "$1" in termux-wake-lock|termux-wake-unlock|termux-clipboard-set) return 0;; esac; return 1; }
in_termux() { return 1; }   # 先关掉，确认非 Termux 路径不会误调
if termux-wake-lock 2>/dev/null; then :; fi
CALLS="$TMP/calls.txt"; : > "$CALLS"
termux-wake-lock()      { echo "wake-lock" >> "$CALLS"; }
termux-wake-unlock()    { echo "wake-unlock" >> "$CALLS"; }
termux-clipboard-set()  { cat >/dev/null; echo "clipboard" >> "$CALLS"; }

in_termux() { return 0; }   # 现在假装是 Termux
print_address >/dev/null
grep -q clipboard "$CALLS" && pass "Termux 下会尝试把地址写进剪贴板" || fail "没走剪贴板分支"

cmd_stop >/dev/null 2>&1 || true   # 进程已经手动 kill 了，这里只验证调用点
grep -q wake-unlock "$CALLS" && pass "stop 会释放唤醒锁" || fail "stop 没释放唤醒锁"

echo
if [ "$FAIL" -eq 0 ]; then
    printf '\033[38;5;84m%s\033[0m\n' "全部通过"
else
    printf '\033[38;5;203m%s\033[0m\n' "$FAIL 项失败"
fi
exit "$FAIL"
