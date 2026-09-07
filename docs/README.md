# TradingAgents-CN 文档

当前代码版本为 **v1.0.0-preview**。以下文档是项目现状的权威入口：

- [系统架构](../ARCHITECTURE.md)
- [开发指南](development.md)
- [测试指南](testing.md)
- [技术债务](technical-debt.md)

## 当前专题

- `architecture/`：历史架构设计和迁移背景；当前边界以根目录
  `ARCHITECTURE.md` 为准。
- `configuration/`：数据源、LLM 和部署配置专题。
- `development/`：早期开发记录和专题指南；当前命令以
  `docs/development.md` 为准。
- `features/`、`guides/`、`usage/`：功能说明和用户指南。
- `releases/`：历史版本说明和升级记录。
- `superpowers/specs/`、`superpowers/plans/`：已评审设计和实施计划。

## 阅读约定

版本化发布说明用于解释当时的行为，可能不再与当前代码一致。修改运行
架构、开发命令、测试范围或已知债务时，应在同一个提交中更新上方对应的
权威文档。
