# Frontend Dockerfile
# Supports two targets:
#   --target dev   — install deps only, run `pnpm dev` at container start
#   --target prod  — full build baked in, run `pnpm start` at container start (default if no --target is specified)

ARG PNPM_STORE_PATH=/root/.local/share/pnpm/store
ARG NPM_REGISTRY

# ── Base: shared setup ────────────────────────────────────────────────────────
FROM node:22-alpine AS base
ARG PNPM_STORE_PATH
ARG NPM_REGISTRY
# Configure corepack registry before installing pnpm so the download itself
# succeeds in restricted networks (COREPACK_NPM_REGISTRY controls where
# corepack fetches package managers from).
RUN if [ -n "${NPM_REGISTRY}" ]; then \
      export COREPACK_NPM_REGISTRY="${NPM_REGISTRY}"; \
    fi && \
    corepack enable && corepack install -g pnpm@10.26.2
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
# Optionally override npm registry for restricted networks (e.g. NPM_REGISTRY=https://registry.npmmirror.com)
RUN if [ -n "${NPM_REGISTRY}" ]; then pnpm config set registry "${NPM_REGISTRY}"; fi
WORKDIR /app
COPY . .

# ── Dev: install only, CMD is overridden by docker-compose ───────────────────
FROM base AS dev
ENV CI=true
RUN cd /app && pnpm install --frozen-lockfile
EXPOSE 3000

# ── Builder: install + compile Next.js ───────────────────────────────────────
FROM base AS builder
ARG NEXT_PUBLIC_LANGGRAPH_BASE_URL=/api/langgraph-compat
ENV CI=true
ENV NEXT_PUBLIC_LANGGRAPH_BASE_URL=${NEXT_PUBLIC_LANGGRAPH_BASE_URL}
RUN cd /app && pnpm install --frozen-lockfile
# Skip env validation — runtime vars are injected by nginx/container
RUN cd /app && SKIP_ENV_VALIDATION=1 pnpm build

# ── Prod: minimal runtime with pre-built output ───────────────────────────────
FROM node:22-alpine AS prod
ENV CI=true
ARG PNPM_STORE_PATH
ARG NPM_REGISTRY
ARG NEXT_PUBLIC_LANGGRAPH_BASE_URL=/api/langgraph-compat
ENV NEXT_PUBLIC_LANGGRAPH_BASE_URL=${NEXT_PUBLIC_LANGGRAPH_BASE_URL}
RUN if [ -n "${NPM_REGISTRY}" ]; then \
      export COREPACK_NPM_REGISTRY="${NPM_REGISTRY}"; \
    fi && \
    corepack enable && corepack install -g pnpm@10.26.2
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
RUN if [ -n "${NPM_REGISTRY}" ]; then pnpm config set registry "${NPM_REGISTRY}"; fi
WORKDIR /app
COPY --from=builder /app .
EXPOSE 3000
CMD ["sh", "-c", "cd /app && pnpm start"]
