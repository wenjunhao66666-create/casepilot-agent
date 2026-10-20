# 五分钟演示

1. 启动服务并查看 `/health`。健康接口只验证初始化，模型可用性仍需真实聊天验证。
2. 通过管理员 `/knowledge/add` 导入 `examples/demo_knowledge.json` 的完整内容。
3. 输入“登录返回401，同一订阅又重复扣了99元”。检查主辅 Agent、路由原因和回答覆盖范围。
4. 输入“请查询知识库：CasePilot 演示订阅的退款申请规则是什么”。检查 `tools_used`、`knowledge_used` 和实际工具 trace。
5. 执行业务回放，查看断言失败原因和耗时统计。回执检查使用 `--check-idempotency`；报告应同时说明模型、样本量与运行条件。

没有模型额度时，可运行 `python -m pytest -q` 演示工程测试；模拟测试结果不能替代真实端到端结果。
