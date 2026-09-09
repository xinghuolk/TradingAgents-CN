# 部署

本文说明 `v1.0.0-preview` 的当前 Docker Compose 部署；返回[文档目录](../README.md)。

在仓库根目录准备好 `.env` 后启动服务：

```bash
docker compose up -d
docker compose ps
docker compose logs -f backend
```

默认前端地址为 `http://localhost:3000`，后端健康检查为
`http://localhost:8000/api/health`。MongoDB 映射到宿主机 `37017` 端口，Redis 映射到
宿主机 `26379` 端口；容器内部仍分别使用 MongoDB `27017` 与 Redis `6379`。

停止并移除服务容器：

```bash
docker compose down
```

可选的 `management` profile 提供 Redis Commander 和 Mongo Express 管理界面；默认启动
不包含它们。需要时使用：

```bash
docker compose --profile management up -d
```

密钥、数据源与运行时配置见[配置](configuration.md)；异常处理见[故障排除](troubleshooting.md)。
