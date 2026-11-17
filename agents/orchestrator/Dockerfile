# Build context: repository root, so agents/shared is available (REQ-CI-07).
# docker build -f agents/orchestrator/Dockerfile .
# One image, two entrypoints: the API (default) and the watcher
# (args: ["-m", "kiroween_orchestrator.watcher"]).

FROM public.ecr.aws/docker/library/python:3.12-slim AS build
RUN pip install --no-cache-dir uv==0.10.10
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY agents/shared agents/shared
COPY agents/orchestrator agents/orchestrator
WORKDIR /src/agents/orchestrator
# --frozen: install exactly the committed lock file; --no-editable copies shared in.
RUN uv sync --frozen --no-dev --no-editable

FROM public.ecr.aws/docker/library/python:3.12-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8080 9090
ENTRYPOINT ["python"]
CMD ["-m", "kiroween_orchestrator"]
