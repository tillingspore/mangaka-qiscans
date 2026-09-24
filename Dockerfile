FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY qiscans ./qiscans
RUN pip install --no-cache-dir .

FROM base AS test
COPY tests ./tests
RUN pip install --no-cache-dir 'pytest==8.3.5' && python -m pytest -q && touch /tests-passed

FROM base AS runtime
COPY --from=test /tests-passed /tests-passed
USER 65534:65534
EXPOSE 3002
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3002/api/health', timeout=3)"
CMD ["gunicorn", "--bind", "0.0.0.0:3002", "--workers", "1", "--threads", "4", "--timeout", "120", "qiscans.app:create_app()"]
