FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# pip 源可覆盖：本机 Docker 直连 PyPI 时大响应会被截断（见 docs/踩坑记录.md），
# 需传国内镜像源；云端不传则用官方源。由 docker-compose.yml 的 build.args 注入。
ARG PIP_INDEX_URL=https://pypi.org/simple

COPY requirements.txt .
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
