# Windows 图形版常见问题

本文适用于 GitHub Release 中的 `roleplay-slim-windows-x64.zip`。先确认已经**完整解压**，不要直接在压缩包预览窗口中运行 EXE。

## Windows 阻止运行或显示 SmartScreen

当前测试版尚未进行代码签名。请只从本项目的 [GitHub Release](https://github.com/lucifergzsz414/roleplay-slim/releases) 下载，先用附带的 `.sha256` 文件核对 ZIP，再决定是否运行。不要从网盘、群文件或第三方重新打包版本启动。

## 聊天软件连不上 `127.0.0.1`

1. 确认启动器显示绿色“运行中”。
2. API 地址应与界面显示完全一致，默认是 `http://127.0.0.1:8795/v1`。
3. 浏览器访问 `http://127.0.0.1:8795/healthz`，正常时会看到 `{"status":"ok"}`。
4. 如果健康检查失败，关闭其他 roleplay-slim 窗口后重新打开。不要随意结束不认识的系统进程。

## 端口被占用或启动失败

通常是另一个启动器或代理仍在运行。先关闭旧窗口，再重试。高级用户可以在 PowerShell 中只读检查：

```powershell
Get-NetTCPConnection -LocalPort 8795 -State Listen -ErrorAction SilentlyContinue
```

当前 Windows 图形版固定使用 `8795`，不能在界面中切换端口。如果占用者是另一个 roleplay-slim 窗口，请正常关闭那个窗口；如果是其他应用，请先关闭或重新配置已确认的冲突应用，或改用可配置端口的 Python 代理。不要直接终止未知进程。

## HTTP 401 或 403

- `401` 通常表示聊天软件没有发送 API Key，或密钥无效。
- `403` 通常表示服务商账户、区域、模型或密钥权限不允许这次请求。

启动器不会读取或保存 API Key。请在原聊天软件中检查服务商密钥和认证设置，不要把真实密钥粘贴到 Issue、截图或日志里。

## HTTP 404 或“模型不存在”

检查服务商地址和模型名是否属于同一家服务商。不确定时先把启动器中的“模型名称”留空，让它沿用聊天软件请求里的模型；只有服务商明确要求固定模型名时才填写。

## HTTP 429

服务商正在限速，或账户额度不足。等待后重试，并在服务商控制台检查配额。roleplay-slim 不会绕过服务商的配额和速率限制。

## HTTP 502 `failed to connect to upstream`

本地代理已经收到请求，但无法连接模型服务商。依次检查：

1. 服务商当前是否可用。
2. 启动器中的上游地址是否正确，公网地址必须使用 HTTPS。
3. 当前 VPN、代理或网络是否能直接访问该服务商。
4. 暂时把聊天软件恢复为服务商原地址，确认不经过 roleplay-slim 时是否也失败。

如果直连同样失败，问题通常不在 roleplay-slim。

## 显示节省 0%

短对话或最近几轮本来就会原样保留，因此 0% 不一定是故障。随着历史变长，旧轮次进入可整理范围后才会出现节省。稳定的 persona/system 前缀也会故意保持逐字节不变。

## 关闭后是否还有后台进程

正常关闭启动器只会停止由当前窗口启动的代理，不会接管更早遗留的进程。若端口检查显示仍有监听，请先记录 PID，并在任务管理器中核对它确实是 `roleplay-slim-proxy.exe` 后只结束该进程；无法确认时请保留 PID 和错误信息提交 Issue。不要批量终止 Python 或系统进程。

## 仍然无法解决

使用 [错误报告表单](https://github.com/lucifergzsz414/roleplay-slim/issues/new?template=01-bug-report.yml)，填写版本、服务商、状态码和最小复现步骤。提交前必须删除 API Key、Authorization 请求头、完整聊天正文、persona、私有地址和账号信息。
