# MiGPT 精简构建：只保留运行需要的 Node、生产依赖和 Rust 原生模块。
# 适用于源码构建，尤其是 256MB 内存的 ARM 设备。

# ---------- 1. 编译 Rust 原生模块 ----------
FROM node:22-bookworm AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

COPY examples/migpt/package.json examples/migpt/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

COPY examples/migpt ./
COPY packages/client-rust ./client-rust

RUN sed -i 's|\.\./\.\./packages/||g' Cargo.toml && pnpm build

# ---------- 2. 只安装运行时依赖（含 tsx） ----------
FROM node:22-bookworm-slim AS runtime-deps

WORKDIR /deps

COPY examples/migpt/package.json examples/migpt/pnpm-lock.yaml ./

# tsconfig 继承了 @mi-gpt/config，tsx 运行时还需要 tsx
RUN node -e "const fs=require('fs');const p=JSON.parse(fs.readFileSync('package.json','utf8'));p.dependencies=p.dependencies||{};p.dependencies.tsx='4.19.3';p.dependencies['@mi-gpt/config']='1.1.0';fs.writeFileSync('package.json',JSON.stringify(p,null,2))" \
    && corepack enable \
    && pnpm install --prod --no-frozen-lockfile

# ---------- 3. 运行镜像 ----------
FROM node:22-bookworm-slim

WORKDIR /app

ENV NODE_ENV=production \
    NODE_OPTIONS="--max-old-space-size=128 --max-semi-space-size=8"

COPY --from=runtime-deps /deps/node_modules ./node_modules
COPY --from=runtime-deps /deps/package.json ./package.json
COPY --from=builder /app/migpt ./migpt
COPY --from=builder /app/tsconfig.json ./tsconfig.json
COPY --from=builder /app/package.json ./package.json

EXPOSE 4399

CMD ["node", "/app/node_modules/tsx/dist/cli.mjs", "migpt/index.ts"]
