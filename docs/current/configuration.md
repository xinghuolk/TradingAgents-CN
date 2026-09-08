# 配置

本文说明 `v1.0.0-preview` 的当前配置方式；返回[文档目录](../README.md)。

## 首次配置

将 `.env.example` 复制为 `.env`。`.env` 是含密钥的启动配置文件，不要提交、分享
或粘贴其内容。至少检查 MongoDB、Redis、`JWT_SECRET`、`CSRF_SECRET`，并配置至少
一个 LLM 提供商的密钥。中国股票数据源由 `DEFAULT_CHINA_DATA_SOURCE` 选择，当前可选
`akshare`、`tushare` 或 `baostock`。

```bash
cp .env.example .env
```

生产环境必须替换模板中的 JWT 和 CSRF 密钥，并按所选数据源补充所需凭据。

## 运行时设置

登录后进入“设置”页面，使用“配置管理”维护模型、数据源和 API 密钥。这是日常运行时
配置的常用入口；应用会在分析前将适用配置传递给分析核心。

`.env.example` 还保留代理及 `NO_PROXY`、财报提取器和 OAuth 订阅鉴权相关的说明。
仅在需要相应功能时按模板填写，尤其要妥善保存 `OAUTH_ENCRYPTION_KEY`；不要在文档、
问题单或日志中复制任何密钥。

配置边界和开发侧映射见[系统架构](../../ARCHITECTURE.md)，启动方式见[快速开始](getting-started.md)。
