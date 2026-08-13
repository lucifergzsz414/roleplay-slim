# roleplay-slim 踩坑记录

## 本仓库不能用 `python -m build`，根目录的 `build.py` 会抢走模块名

**日期**：2026-08-13

**现象**：想构建 sdist/wheel，跑 `python -m build`，结果输出的是
`安装器.exe` / `卸载还原器.exe` / `roleplay-slim-proxy.exe` 三行路径，
`dist/` 里没有任何 `.tar.gz`。

**根因**：`-m` 会先在当前工作目录找同名模块，而本仓库根目录有一个
**已被 git 跟踪的 `build.py`**（PyInstaller 打包脚本，产出桌宠安装器）。
它把标准的 `build` 包整个盖掉了，于是"构建 Python 包"变成了"构建三个 exe"。
不报错，只是干了完全不同的事。

**解决**：构建发布包一律用 `hatch build`，不要用 `python -m build`。

## `dist/` 不是纯构建产物目录，`rm -rf dist` 会删掉分发包

**日期**：2026-08-13

**现象**：清理构建产物时习惯性 `rm -rf dist`，之后发现
`dist/若叶睦桌宠-上下文优化代理.zip` 和 `dist/邦多利桌宠-上下文优化代理.zip`
没了——那是桌宠用户实际下载安装的东西，不是中间产物。

**根因**：这个仓库的 `dist/` 混放三类东西：hatch 产出的 sdist/wheel、
PyInstaller 产出的 exe、以及给最终用户的分发 zip。`dist/` 在 `.gitignore` 里，
所以 git 救不回来。

**代价**：重建要跑两趟 PyInstaller（约几分钟），且必须用全局 Python
（`AppData\Local\Programs\Python\Python312`，见文末"PyInstaller构建环境"），
venv 里没装 pyinstaller。

**重建命令**（两趟，顺序不能反）：
```
python build.py                                    # 若叶睦三件套 exe
python build.py --bandori-only --zip --bandori-zip # Bandori两个exe + 打两个zip
```
第二趟带了 `--bandori-only`，`any_only` 为真会导致若叶睦三件套**不重建**，
所以第一趟必须先跑完。

**预防**：要清理只删具体文件，别整个删目录。

## 四个 BandoriPet 文件不是"遗留垃圾"，别搬也别删

**日期**：2026-08-13

`install_bandori_gui.py` / `uninstall_bandori_gui.py` /
`installer_bandori/patch_bandori.py` / `使用说明_BandoriPet.txt` 长期以
untracked 状态躺在根目录，看着像误放的杂物，其实是本项目给 BandoriPet
桌宠写的安装器，跟根目录那套若叶睦的（`install_gui.py` / `installer/` /
`使用说明.txt`）是孪生结构，`build.py` 第 42-49 行按根目录路径写死。

曾经把它们挪进 `integrations/bandoripet/`，连带弄坏两处：`build.py` 的
`BANDORI_*_SRC` 常量全部失效；两个 GUI 里的
`sys.path.insert(0, _bundle_dir / "installer_bandori")` 指向了已删目录。
**已撤销，恢复根目录布局并纳入 git 跟踪。**

它们当初进过 0.3.1 的 sdist，但那是打包配置的问题，**已由
`pyproject.toml` 的 `[tool.hatch.build.targets.sdist] include` 白名单
解决**——根目录放什么都不会再被扫进发布包。所以没有任何理由再动它们的位置。

真想整理，得把若叶睦那套一起挪，并同步改 `build.py`、本文件、以及
`dist/_build_*/` 里引用源码路径的 PyInstaller spec，属于独立重构。

## 代理转发响应时忘删 Content-Encoding，导致 UnityWebRequest 报 "Unrecognized content-encoding"

**日期**：2026-08-02

**现象**：BandoriPet/若叶睦桌宠通过 `roleplay-slim-proxy` 转发到 DeepSeek 时，Unity端
报 `Unrecognized content-encoding`，主对话/理解器/Planner全部失败，表现成"网络连接错误"。

**根因**：`src/roleplay_slim/proxy/server.py` 用 `httpx.AsyncClient` 请求上游，httpx
会按上游 `Content-Encoding`（gzip/br/deflate）**透明自动解压**——`resp.content` /
`resp.aiter_bytes()` 拿到的已经是解压后的明文字节。但转发响应头时只排除了
`_HOP_BY_HOP_HEADERS`（host/content-length/connection等），`content-encoding` 不在
里面，于是原样转发给客户端。客户端收到"标着br/gzip但实际是明文"的body——
`UnityWebRequest` 不认brotli，直接报错；换成gzip协商则报"GZip magic number不对"
（同一个bug的另一种报错形式）。

**修复**：把 `content-encoding` 加进 `_HOP_BY_HOP_HEADERS`（第30-34行），非流式
（约220-223行）和流式（约254-257行）两处响应头过滤复用同一个集合，一次改动两处生效。

**验证方式**：真实网络请求在这个工具沙箱环境里连不上外部API（对新构建的exe有出站
限制，跟bug本身无关），改用 `httpx.MockTransport` 模拟一个真正返回
`gzip`压缩体+`Content-Encoding: gzip`头的上游response（跟生产环境的br机制同源，
gzip不需要额外装brotli库就能在标准库里复现），确认修复后代理响应里
`content-encoding`消失、body正确解码。这比走真实API更干净、更确定，以后遇到
"httpx透明解压+转发响应头"这类代理bug可以照搬这个验证方法。

**部署方式的坑**：这个项目会打包成 PyInstaller 独立exe（`dist/roleplay-slim-proxy.exe`），
桌宠装的是打包版的zip分发包（`dist/若叶睦桌宠-上下文优化代理.zip` 和
`dist/邦多利桌宠-上下文优化代理.zip`），**只改源码不够**——必须重新
`pyinstaller dist\_build_proxy\roleplay-slim-proxy.spec --distpath dist
--workpath dist\_build_proxy --noconfirm` 重新构建exe，再把新exe塞回这两个zip里
（zip里其他文件——安装器/卸载器/使用说明——不要动，用 `zipfile` 只替换单个entry，
不要整个重新打包覆盖）。

**PyInstaller构建环境**：这份 `.venv` 里没装 `pyinstaller`，用的是全局Python环境
（`AppData\Local\Programs\Python\Python312`）里装的 `pyinstaller` + `httpx`/`fastapi`/
`uvicorn`，构建命令直接跑 `pyinstaller <spec文件>` 用全局环境即可，不用额外装依赖。
