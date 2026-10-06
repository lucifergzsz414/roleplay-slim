#!/usr/bin/env bash
# 注意：Termux 没有 /usr/bin/env，直接执行这个文件会 bad interpreter。
# 一律用 `bash roleplay-slim.sh` 调用（README 里也是这么写的），
# 这样也不需要手动 chmod +x。
#
# roleplay-slim 手机版（Termux / Android）
#
# 给手机酒馆（SillyTavern）、OpenWebUI 之类能填 API 地址的 app 用：
# 装一次，之后每次运行就把代理起起来，告诉你该往 app 里填哪个地址，
# 然后实时显示这次对话少发了多少内容。
#
#   bash roleplay-slim.sh            # 没装就装，装了就启动
#   bash roleplay-slim.sh install    # 只做一次性安装
#   bash roleplay-slim.sh start      # 启动并实时看数字
#   bash roleplay-slim.sh status     # 看一眼当前数字就退出
#   bash roleplay-slim.sh url        # 只打印要填进 app 的地址
#   bash roleplay-slim.sh stop       # 停掉
#
# 跟桌面版一样，这个脚本不要你的 API key —— 你的 key 填在酒馆/app 里，
# 代理会把 app 自己带的凭据原样转发给上游，从头到尾看不到、也不保存。

set -u

PORT="${ROLEPLAY_SLIM_PORT:-8796}"
WORKDIR="${ROLEPLAY_SLIM_HOME:-$HOME/.roleplay-slim}"
CONFIG="$WORKDIR/config.toml"
PIDFILE="$WORKDIR/proxy.pid"
LOGFILE="$WORKDIR/proxy.log"
DEFAULT_UPSTREAM="https://api.deepseek.com/v1"

# ── 输出小工具 ────────────────────────────────────────────────
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
    C_ACCENT=$'\033[38;5;141m'; C_OK=$'\033[38;5;84m'
    C_WARN=$'\033[38;5;222m'; C_ERR=$'\033[38;5;203m'
else
    C_RESET=""; C_DIM=""; C_BOLD=""; C_ACCENT=""; C_OK=""; C_WARN=""; C_ERR=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_OK" "$C_RESET" "$*"; }
warn() { printf '%s!%s %s\n' "$C_WARN" "$C_RESET" "$*"; }
err()  { printf '%s✗%s %s\n' "$C_ERR" "$C_RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# ── 配置 ─────────────────────────────────────────────────────
write_config() {
    local upstream="$1"
    mkdir -p "$WORKDIR" || die "无法创建 $WORKDIR"
    cat > "$CONFIG" <<EOF
# roleplay-slim 手机版自动生成，不需要手动改
[proxy]
upstream_base_url = "$upstream"
upstream_api_key_env = "ROLEPLAY_SLIM_UNUSED_KEY"
host = "127.0.0.1"
port = $PORT

[compressor]
keep_recent_turns = 6
enable_whitespace_normalize = true
enable_dedupe_verbatim_tail = true
enable_history_window = true
enable_strip_stage_directions = false
history_window_mode = "trim"

[stats]
persist = true
db_path = "$WORKDIR/stats.db"
EOF
}

# 读已配置的上游；没配置就返回默认值
read_upstream() {
    if [ -f "$CONFIG" ]; then
        sed -n 's/^upstream_base_url *= *"\(.*\)"/\1/p' "$CONFIG" | head -1
    fi
}

proxy_url() { printf 'http://127.0.0.1:%s/v1' "$PORT"; }

# ── 安装 ─────────────────────────────────────────────────────
is_installed() {
    have python && python -c 'import roleplay_slim' >/dev/null 2>&1
}

in_termux() {
    [ -n "${TERMUX_VERSION:-}" ] || [ -d /data/data/com.termux/files/usr ]
}

cmd_install() {
    say "${C_BOLD}roleplay-slim 手机版 · 一次性安装${C_RESET}"
    say ""

    # 先建目录：下面的 pip 错误日志要往这里写，而 write_config() 是后面才调的，
    # 首次安装时目录还不存在，重定向会直接失败。
    mkdir -p "$WORKDIR" || die "无法创建 $WORKDIR"

    if ! have python; then
        say "正在安装 Python…"
        if in_termux; then
            pkg update -y >/dev/null 2>&1 || true
            pkg install -y python || die "Python 安装失败"
        else
            die "没找到 python，也不是 Termux 环境，请先自行安装 Python 3.9+"
        fi
    fi
    ok "Python 就绪（$(python -V 2>&1)）"

    if is_installed; then
        ok "roleplay-slim 已经装过了"
    else
        say "正在安装 roleplay-slim…"
        # 新版 pip 会把系统 Python 标成 externally-managed 并拒绝安装；
        # Termux 上这是常态，所以失败后按提示重试一次。
        if ! python -m pip install --quiet --upgrade roleplay-slim 2>"$WORKDIR/pip.err"; then
            if grep -q "externally-managed" "$WORKDIR/pip.err" 2>/dev/null; then
                python -m pip install --quiet --break-system-packages --upgrade roleplay-slim \
                    || die "roleplay-slim 安装失败，详见 $WORKDIR/pip.err"
            else
                err "roleplay-slim 安装失败，详见 $WORKDIR/pip.err"
                exit 1
            fi
        fi
        ok "roleplay-slim 已安装"
    fi

    if [ ! -f "$CONFIG" ]; then
        write_config "$DEFAULT_UPSTREAM"
        ok "配置已写入 $CONFIG"
        say "  ${C_DIM}默认连 DeepSeek；换服务商就编辑那一行 upstream_base_url${C_RESET}"
    fi

    if in_termux && have termux-wake-lock; then
        ok "已请求后台保持运行权限"
    fi
    say ""
    ok "装好了。运行 ${C_BOLD}bash roleplay-slim.sh${C_RESET} 就能启动。"
}

# ── 启停 ─────────────────────────────────────────────────────
is_running() {
    [ -f "$PIDFILE" ] || return 1
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null
}

# 端口上有没有东西在应答（可能不是本脚本起的）
probe_port() {
    python - "$PORT" <<'PY' >/dev/null 2>&1
import sys, urllib.request
port = sys.argv[1]
try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.5)
except Exception:
    sys.exit(1)
PY
}

cmd_start() {
    is_installed || cmd_install
    say ""

    if is_running; then
        ok "代理已经在跑了"
    elif probe_port; then
        warn "端口 $PORT 上已经有一个代理在跑（不是这个脚本启动的）"
    else
        # 该哪个命令跑：装了命令行入口就用它，否则用 python -m
        local runner
        if have roleplay-slim-proxy; then
            runner="roleplay-slim-proxy"
        else
            runner="python -m roleplay_slim.proxy"
        fi

        # Android 会随时冻结后台进程，没有唤醒锁的话聊到一半代理就没了
        if in_termux && have termux-wake-lock; then
            termux-wake-lock 2>/dev/null || true
        fi

        say "正在启动…"
        # shellcheck disable=SC2086
        nohup $runner --config "$CONFIG" >>"$LOGFILE" 2>&1 &
        echo $! > "$PIDFILE"

        local i=0
        while [ "$i" -lt 30 ]; do
            if probe_port; then break; fi
            if ! is_running; then
                err "代理启动失败，最后几行日志："
                tail -5 "$LOGFILE" 2>/dev/null | sed 's/^/    /'
                rm -f "$PIDFILE"
                exit 1
            fi
            sleep 0.5
            i=$((i + 1))
        done
        probe_port || die "代理启动超时，详见 $LOGFILE"
        ok "代理已启动"
    fi

    # 唤醒锁只在 Termux 里，且只在真的被占用时提示
    if in_termux && have termux-wake-lock; then
        termux-wake-lock 2>/dev/null || true
    fi

    print_address
    watch_loop
}

cmd_stop() {
    if is_running; then
        kill "$(cat "$PIDFILE")" 2>/dev/null || true
        rm -f "$PIDFILE"
        ok "已停止"
    else
        rm -f "$PIDFILE"
        warn "本来就没在跑"
    fi
    if in_termux && have termux-wake-unlock; then
        termux-wake-unlock 2>/dev/null || true
    fi
}

# ── 地址 & 统计 ───────────────────────────────────────────────
print_address() {
    local url; url="$(proxy_url)"
    say ""
    say "${C_BOLD}把这个地址填进酒馆 / app 的「API 地址」栏：${C_RESET}"
    say ""
    say "    ${C_ACCENT}${C_BOLD}$url${C_RESET}"
    say ""
    if in_termux && have termux-clipboard-set; then
        if printf '%s' "$url" | termux-clipboard-set 2>/dev/null; then
            ok "已复制到剪贴板，直接粘贴就行"
        fi
    fi
    say "${C_DIM}API Key 填你自己的，跟以前一样 —— 这个工具看不到也不会存。${C_RESET}"
    say ""
}

# 取数（网络）和渲染（纯输出）分开：渲染能拿合成数据确定性地测，
# 不用为每次跑测试都花一次真调用的钱。
fetch_stats_json() {
    python - "$PORT" <<'PY' 2>/dev/null
import json, sys, urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats?window=1", timeout=2) as r:
        s = json.loads(r.read().decode("utf-8"))
except Exception:
    sys.exit(1)
rec = s.get("recent") or {}
print(json.dumps({
    "b": rec.get("tokens_before_total", 0) or 0,
    "a": rec.get("tokens_after_total", 0) or 0,
    "saved": s.get("tokens_saved_total", 0) or 0,
    "count": s.get("request_count", 0) or 0,
}))
PY
}

render_stats_from_json() {
    local json="$1"

    python - "$json" <<'PY'
import json, sys

# Adapt to whatever the terminal actually speaks. Deliberately NOT forcing
# UTF-8: on a GBK console that would turn the Chinese into mojibake, which
# is a worse failure than a missing block character. The real trap is that
# GBK carries Chinese but not the block-element range (U+2580-259F), so the
# first version died mid-render with UnicodeEncodeError on the bar itself.
ENC = getattr(sys.stdout, "encoding", None) or "ascii"


def can(ch: str) -> bool:
    try:
        ch.encode(ENC)
        return True
    except Exception:
        return False


# Both glyphs, not just one: GBK happens to carry U+2588 (█) but NOT
# U+2591 (░), so testing only the filled block passes the check and then
# dies on the empty one.
if can("█") and can("░"):
    FULL, EMPTY, ARROW = "█", "░", "→"
    T_LEGEND = "█ 原本要发的    ░ 整理之后实际发的"
    T_SAVED, T_EMPTY = "少发 {pct:.0f}%", "还没有请求经过它 —— 在 app 里发一句话试试"
    T_TOTAL = "累计 {count} 次对话，一共少发 {saved} token"
elif can("少"):
    # CJK codepage (GBK/GB18030…): Chinese fine, block glyphs not.
    FULL, EMPTY, ARROW = "#", "-", "->"
    T_LEGEND = "# 原本要发的    - 整理之后实际发的"
    T_SAVED, T_EMPTY = "少发 {pct:.0f}%", "还没有请求经过它 —— 在 app 里发一句话试试"
    T_TOTAL = "累计 {count} 次对话，一共少发 {saved} token"
else:
    # ASCII-only terminal: English is the only thing that renders at all.
    FULL, EMPTY, ARROW = "#", "-", "->"
    T_LEGEND = "# before        - after"
    T_SAVED, T_EMPTY = "saved {pct:.0f}%", "no traffic yet -- send a message in your app"
    T_TOTAL = "{count} requests, saved {saved} tokens"

RESET = "\033[0m"; DIM = "\033[2m"; BOLD = "\033[1m"
ACCENT = "\033[38;5;141m"; OK = "\033[38;5;84m"

d = json.loads(sys.argv[1])
b, a = d["b"], d["a"]
fmt = lambda n: f"{n:,}"  # noqa: E731

if b:
    ratio = max(0.0, min(1.0, a / b))
    pct = (b - a) / b * 100
    width = 28
    filled = int(round(ratio * width))
    print(f"  {fmt(b)}  {ACCENT}{ARROW}{RESET}  {BOLD}{fmt(a)}{RESET}    "
          f"{OK}{T_SAVED.format(pct=pct)}{RESET}")
    print(f"  {ACCENT}{FULL * filled}{EMPTY * (width - filled)}{RESET}")
    print(f"  {DIM}{T_LEGEND}{RESET}")
else:
    print(f"  {DIM}{T_EMPTY}{RESET}")

if d["count"]:
    print(f"  {DIM}{T_TOTAL.format(count=d['count'], saved=fmt(d['saved']))}{RESET}")
PY
}

# 取数 + 渲染。返回非 0 表示取不到。
render_stats() {
    local json
    json="$(fetch_stats_json)" || return 1
    render_stats_from_json "$json"
}

# ── 交互 ─────────────────────────────────────────────────────
watch_loop() {
    say "${C_DIM}每 2 秒刷新一次，按 Ctrl-C 退出（代理会继续在后台跑）${C_RESET}"
    say ""
    # 保住用户的 Ctrl-C 不会被吞掉
    trap 'say ""; say "${C_DIM}代理还在后台跑着，要用 stop 才能停。${RESET}"; exit 0' INT
    while true; do
        printf '\033[2J\033[H'   # 清屏回到左上角，避免刷屏污染滚动历史
        print_address_header
        if ! render_stats; then
            warn "连不上代理了（可能被系统杀掉了）"
            exit 1
        fi
        say ""
        say "${C_DIM}Ctrl-C 退出${C_RESET}"
        sleep 2
    done
}

print_address_header() {
    local url; url="$(proxy_url)"
    printf '%s\n' "${C_BOLD}roleplay-slim 正在运行${C_RESET}"
    printf '%s\n\n' "地址：${C_ACCENT}$url${C_RESET}"
}

cmd_status() {
    if ! probe_port; then
        warn "代理没在跑（用 start 启动）"
        return 1
    fi
    print_address_header
    render_stats || { warn "取统计失败"; return 1; }
}

cmd_url() { printf '%s\n' "$(proxy_url)"; }

cmd_help() {
    # 打印文件开头的注释块，遇到第一行代码就停 —— 固定行号范围会在
    # 注释增删时把 `set -u`、变量赋值之类的代码一起打出来。
    awk 'NR > 1 { if (/^#/) { sub(/^# ?/, ""); print } else { exit } }' "$0"
}

# ── 入口 ─────────────────────────────────────────────────────
main() {
    case "${1:-}" in
        install) cmd_install ;;
        start)   cmd_start ;;
        stop)    cmd_stop ;;
        status)  cmd_status ;;
        url)     cmd_url ;;
        help|-h|--help) cmd_help ;;
        "")
            # 默认：没装就装，装了就启动
            cmd_start
            ;;
        *) err "未知命令：$1"; say ""; cmd_help; exit 2 ;;
    esac
}

# 被 source 时不自动执行，方便测试直接调里面的函数
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
