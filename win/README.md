# Windows 版（预留）

> **尚未实现。** 本目录留给 Windows 微信聊天记录导出工具。

## 与 macOS 的边界

| | macOS（现有） | Windows（本目录） |
| --- | --- | --- |
| 入口 | 仓库根目录 `mac-test.py` | 待定，如根目录 `win-test.py` |
| 实现 | `mac/` | `win/`（本目录） |
| 取钥 | `codesign` + LLDB / 内存扫描 | 不同机制（勿复用 mac 脚本） |
| 数据路径 | `~/Library/Containers/.../xwechat_files/` | Windows 微信本地目录 |

请勿：

- 把 `mac/` 下的脚本拿到 Windows 跑
- 把 Windows 实现文件塞进 `mac/`
- 在共用模块里混入仅某一平台可用的取钥逻辑（平台相关逻辑放各自目录）

实现完成后再补本目录的环境部署与使用说明。
