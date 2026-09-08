# 故障排除

本文说明 `v1.0.0-preview` 的当前排查入口；返回[文档目录](../README.md)。

使用 Compose 时先检查服务健康状态和日志：

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f mongodb
docker compose logs -f redis
```

- 应用启动失败：核对 `.env` 的必需项和后端日志，确认 MongoDB、Redis 均可连接。
- MongoDB 或 Redis 异常：查看对应容器日志、健康状态及宿主机端口冲突；不要把容器内端口
  与宿主机映射端口混用。
- LLM 或数据提供商失败：确认已配置至少一个可用提供商、密钥有效，以及代理和 `NO_PROXY`
  设置符合网络环境。
- 前端页面或资源失败：查看 frontend 容器日志；本地源码改动后，按[开发指南](../development.md)
  运行相应验证，并可执行完整前端 bundle 检查。

修改源码后运行完整的仓库 harness：

```bash
python scripts/harness.py
```

提交问题时提供可复现步骤、脱敏后的错误信息和相关服务日志。绝不要把 API 密钥、JWT、
CSRF 密钥、OAuth 凭据或完整 `.env` 粘贴到问题单或日志中。
