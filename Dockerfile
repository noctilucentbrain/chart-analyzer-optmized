FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml readme.md constraints.txt ./
COPY src ./src
COPY examples ./examples

RUN pip install --no-cache-dir -c constraints.txt .

EXPOSE 8000


ENV CHART_ANALYZER_CONFIG=examples/config.yaml
ENV CHART_ANALYZER_HOST=0.0.0.0
ENV CHART_ANALYZER_PORT=8000


CMD ["sh", "-c", "chart-analyzer api --config \"$CHART_ANALYZER_CONFIG\" --host \"$CHART_ANALYZER_HOST\" --port \"$CHART_ANALYZER_PORT\""]
