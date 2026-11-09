# syntax=docker/dockerfile:1
FROM python:3.13.5-slim-bookworm
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --require-hashes -r requirements.lock
COPY . .
RUN pip install --no-deps --no-build-isolation -e .
CMD ["eri", "--help"]
