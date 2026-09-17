FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# pip 源可覆盖：本机 Docker 直连 PyPI 时大响应会被中间层截断（实测踩过），
# 需传国内镜像源；云端不传则用官方源。由 docker-compose.yml 的 build.args 注入。
ARG PIP_INDEX_URL=https://pypi.org/simple

COPY requirements.txt .
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} -r requirements.txt

COPY app ./app

# 迁移文件也进镜像（原先漏了，容器里 `python -m migrations.apply` 会 ModuleNotFoundError）。
# 注意 apply.py 内部执行的是 `docker exec <pg容器> psql`，所以它得在**宿主机**上跑
# —— backend 容器里没有 docker CLI：
#   cd backend && KP_PG_CONTAINER=knowpilot-postgres python3 migrations/apply.py <文件>.sql
COPY migrations ./migrations

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
