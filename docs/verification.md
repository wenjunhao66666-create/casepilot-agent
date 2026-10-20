# 本地验证记录

日期：2026-10-20。环境：Windows，Python 3.12，复用本机已安装的项目依赖。

| 检查 | 结果 |
| --- | --- |
| `python -m pytest tests -q` | 101 passed，9.71 秒 |
| `docker compose config --quiet` | 通过，使用示例配置占位符 |
| `python examples/replay.py --help` | 命令入口正常 |
| `git diff --check` | 通过 |

测试覆盖既有 Agent、意图、工具、MCP、记忆并发、评测，以及新增的业务回放与订阅 Skill。部分测试使用模拟对象和本地 MCP 测试进程。

本记录不代表已执行真实模型业务回放、真实 Redis/Chroma 集成、Docker 完整构建或远端 GitHub Actions。真实回放需要部署服务、导入示例知识和有效模型凭据；其结果应单独保存和报告。
