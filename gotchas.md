# roleplay-slim 踩坑记录

## `hatch build` 没有输出目录参数，产物永远写进项目的 `dist/`

**日期**：2026-08-16

**现象**：`scripts/check_sdist.py` 想构建 sdist 到临时目录，传 `-d` 直接
`Error: No such option: -d`。

**根因**：`hatch build` 只支持 `-t/--target`、`-a/--all`、`-c/--clean` 等，
产物一律写进项目（`pyproject.toml` 所在目录）的 `dist/`。

**解决**：check_sdist.py 用「构建前把 `dist/` 里已有的 `roleplay_slim-*.tar.gz`
备份到临时目录 → 构建 → 校验 → 恢复/清理」模式，保证 `dist/` 不被污染。
**绝对不要用 `-c/--clean`** —— 那会删掉 `dist/` 里的端用户分发 zip
（若叶睦/邦多利桌宠安装包），git 救不回来。

## subprocess 读 `hatch build` 输出在 Windows 会 GBK 解码崩溃

**日期**：2026-08-16

**现象**：`subprocess.run(["hatch","build",...], capture_output=True, text=True)`
抛 `UnicodeDecodeError: 'gbk' codec can't decode byte ...`，连 traceback 都
出现在 reader 线程里。

**根因**：`text=True` 用系统 locale（Windows 中文 = GBK）解码 hatch 的输出，
而 hatch 的日志里有 `—`（em dash）等非 GBK 字符。

**解决**：必须显式 `encoding="utf-8", errors="replace"`。所有 `subprocess.run`
调用带 `text=True` 的都要这么写，不只在 hatch 上。

## hatchling 会把 VCS 文件（`.gitignore` 等）和 `.hypothesis/` 强塞进 sdist

**日期**：2026-08-16

**现象**：sdist 洁净度门槛（check_sdist.py）首次运行就抓出两个白名单外的文件：
`.gitignore` 和 `.hypothesis/.gitignore`。前者在 0.3.2 里就一直在，后者是
0.3.1 事故「.hypothesis 测试缓存混进发布包」的残留。

**根因**：hatchling 对 sdist 的 VCS 文件（`.gitignore`/`.gitattributes` 等）是
**自动包含**，allowlist（`include`）拦不住；`.hypothesis/` 里那层 `.gitignore`
也是被这个自动包含带进去的。allowlist 是「除了白名单都要有原因」，不是「白名单
外的都进不来」。

**解决**：`.gitignore` 在 pyproject 的 sdist `include` 里显式声明（诚实地承认它
总是会进）；`.hypothesis/` 和 `__pycache__/` 加进 sdist `exclude` 数组，并把
`.hypothesis/` 加进仓库 `.gitignore`。**check_sdist.py 就是为抓这种漏网之鱼写的**——
它已经两次证明自己有用（0.3.1 同款垃圾至今还在）。

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

**2026-08-20 更新**：`build.py` 已挪到 `integrations/pet-installer/build.py`（见下方
"BandoriPet 文件"条目），不再躺在仓库根——这个坑现在从根目录跑 `python -m build`
**不会再发生**。仍保留本记录：坑本身的教训（`-m` 优先找 CWD 同名模块）在别的项目
可能重现，而且历史上确实炸过一次。

## `dist/` 不是纯构建产物目录，`rm -rf dist` 会删掉分发包

**日期**：2026-08-13

**现象**：清理构建产物时习惯性 `rm -rf dist`，之后发现
`dist/若叶睦桌宠-上下文优化代理.zip` 和 `dist/邦多利桌宠-上下文优化代理.zip`
没了——那是桌宠用户实际下载安装的东西，不是中间产物。

**根因**：这个仓库的 `dist/` 混放三类东西：hatch 产出的 sdist/wheel、
PyInstaller 产出的 exe、以及给最终用户的分发 zip。`dist/` 在 `.gitignore` 里，
所以 git 救不回来。

**2026-08-20 更新**：`build.py` 挪到 `integrations/pet-installer/` 后，PyInstaller
产物和分发 zip 现在写进 `integrations/pet-installer/dist/`，跟仓库根的
`dist/`（只剩 hatch 的 sdist/wheel）**彻底分开**了。三类东西混一个目录的根因已解决，
`rm -rf dist`（仓库根）现在只会删 PyPI 产物，不会删桌宠分发包了——但两边都还是
"删了 git 救不回来"，`rm -rf dist` 之前先看清是哪个 `dist/`，这条预防仍然成立。

**代价**：重建要跑两趟 PyInstaller（约几分钟），且必须用全局 Python
（`AppData\Local\Programs\Python\Python312`，见文末"PyInstaller构建环境"），
venv 里没装 pyinstaller。

**重建命令**（两趟，顺序不能反；2026-08-20 起路径改为
`integrations/pet-installer/build.py`，需先 `cd integrations/pet-installer`）：
```
cd integrations/pet-installer
python build.py                                    # 若叶睦三件套 exe
python build.py --bandori-only --zip --bandori-zip # Bandori两个exe + 打两个zip
```
第二趟带了 `--bandori-only`，`any_only` 为真会导致若叶睦三件套**不重建**，
所以第一趟必须先跑完。产物现在落在 `integrations/pet-installer/dist/`，不再是仓库根
`dist/`（那里现在只放 PyPI 的 sdist/wheel）。

**预防**：要清理只删具体文件，别整个删目录。

## BandoriPet 工具链已完整并入 integrations/pet-installer/（2026-08-13 失败尝试的后续）

**日期**：2026-08-13（首次尝试，失败并撤销）→ 2026-08-20（完整重做，成功）

2026-08-13 只想挪 4 个 BandoriPet 文件（`install_bandori_gui.py` /
`uninstall_bandori_gui.py` / `installer_bandori/patch_bandori.py` /
`使用说明_BandoriPet.txt`），结果弄坏两处：`build.py` 的 `BANDORI_*_SRC` 常量全部
失效；两个 GUI 里的 `sys.path.insert(0, _bundle_dir / "installer_bandori")` 指向了
已删目录。当时判断"只挪一半，两套孪生结构就会散架"，撤销并记录"真想整理，得把
若叶睦那套一起挪"。

**2026-08-20 按那条记录整体挪动**，一次性把两套（若叶睦 + BandoriPet）连同
`build.py`、`proxy_entry.py` 一起搬进 `integrations/pet-installer/`：
- `build.py` / `install_gui.py` / `uninstall_gui.py` / `installer/` /
  `install_bandori_gui.py` / `uninstall_bandori_gui.py` / `installer_bandori/` /
  `proxy_entry.py` —— 全部用 `git mv`，历史保留
- 只改了一处代码逻辑：`build.py` 加 `REPO_ROOT = ROOT.parent.parent`，
  `PROXY_PACKAGE` 从 `ROOT / "src" / "roleplay_slim"` 改成
  `REPO_ROOT / "src" / "roleplay_slim"`（因为 `build.py` 现在离 `src/` 隔了两层）
- 两个 GUI 的 `sys.path.insert` 不用动——它们相对 `__file__` 定位 `installer/`，
  整体平移后相对关系不变
- `dist/_build_*/` 里的 PyInstaller spec 不用手动改，是生成产物，下次构建自动重建
- **验证方式**：真跑了一次 `python build.py --proxy-only`（不是只测路径判断），
  确认 `--paths` 正确解析到仓库根 `src/`，产物 25.2MB exe 启动后 `/healthz` 返回
  200，冒烟测试通过后才收尾

`使用说明*.txt` 两个文件本身已在更早的提交里从仓库删除（私有分发内容），
`build.py` 对它们的缺失有专门挡板（见"缺失使用说明"相关条目），这次搬动不受影响。

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

**部署方式的坑**：这个项目会打包成 PyInstaller 独立exe，桌宠装的是打包版的zip分发包
（2026-08-20 起路径是 `integrations/pet-installer/dist/roleplay-slim-proxy.exe`、
`integrations/pet-installer/dist/若叶睦桌宠-上下文优化代理.zip`、
`integrations/pet-installer/dist/邦多利桌宠-上下文优化代理.zip`——之前在仓库根
`dist/` 下，见"BandoriPet 工具链已完整并入"条目），**只改源码不够**——必须
`cd integrations/pet-installer` 后重新
`pyinstaller dist\_build_proxy\roleplay-slim-proxy.spec --distpath dist
--workpath dist\_build_proxy --noconfirm` 重新构建exe，再把新exe塞回这两个zip里
（zip里其他文件——安装器/卸载器/使用说明——不要动，用 `zipfile` 只替换单个entry，
不要整个重新打包覆盖）。

**PyInstaller构建环境**：这份 `.venv` 里没装 `pyinstaller`，用的是全局Python环境
（`AppData\Local\Programs\Python\Python312`）里装的 `pyinstaller` + `httpx`/`fastapi`/
`uvicorn`，构建命令直接跑 `pyinstaller <spec文件>` 用全局环境即可，不用额外装依赖。
