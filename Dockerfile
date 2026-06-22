# ---- Stage 1: builder ----
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --upgrade pip

COPY pyproject.toml .
RUN pip install --prefix=/install .

# ---- Stage 2: runtime ----
FROM python:3.12-slim

WORKDIR /app

RUN addgroup --system --gid 1001 appgroup \
 && adduser --system --uid 1001 --ingroup appgroup appuser

COPY --from=builder /install /usr/local
COPY src/ ./src/

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8010

CMD ["python", "src/server.py"]
