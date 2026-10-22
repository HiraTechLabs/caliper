# syntax=docker/dockerfile:1.7
# Caliper container image. Pinned base by digest and Debian packages by version.
# The runtime stage is the default target; the `test` stage runs the full suite.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

WORKDIR /src
COPY pyproject.toml README.md /src/
COPY caliper /src/caliper
RUN pip wheel --no-deps --wheel-dir /wheels .

# --- full test suite inside the image ---------------------------------------
FROM builder AS test

ARG GIT_VERSION=1:2.39.5-0+deb12u3
ARG OPENSSH_VERSION=1:9.2p1-2+deb12u10
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git="${GIT_VERSION}" openssh-client="${OPENSSH_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pytest==9.1.1
COPY tests /src/tests
COPY scripts /src/scripts
COPY .caliper /src/.caliper
COPY Dockerfile compose.yaml .dockerignore .gitattributes requirements.lock /src/
RUN python -m pytest -q

# --- runtime ----------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ARG GIT_VERSION=1:2.39.5-0+deb12u3
ARG OPENSSH_VERSION=1:9.2p1-2+deb12u10
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git="${GIT_VERSION}" openssh-client="${OPENSSH_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 caliper \
    && useradd --uid 10001 --gid caliper --home-dir /home/caliper \
        --shell /usr/sbin/nologin --no-create-home caliper \
    && mkdir -p /data /run/caliper /app \
    && chown 10001:10001 /data /run/caliper /app \
    && chmod 0700 /run/caliper

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /wheels /wheels
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps /wheels/caliper-*.whl \
    && rm -rf /wheels

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    CALIPER_DATA_DIR=/data \
    CALIPER_RUNTIME_DIR=/run/caliper \
    CALIPER_HOST=0.0.0.0 \
    CALIPER_PORT=8080 \
    CALIPER_LOG_LEVEL=INFO

WORKDIR /app
USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=4)"]

ENTRYPOINT ["python", "-m", "caliper"]
CMD ["serve"]
