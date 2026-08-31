# Nyaya frontend. Multi-stage: deps -> build -> slim runtime.
#
# NEXT_PUBLIC_* values are inlined into the client bundle at BUILD time, not
# read at runtime, so the API base URL has to arrive as a build arg.

FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-bookworm-slim AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN groupadd --gid 1001 nodejs \
 && useradd --uid 1001 --gid nodejs --create-home --shell /usr/sbin/nologin nyaya

# `output: "standalone"` produces server.js plus only the modules it needs.
COPY --from=builder --chown=nyaya:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nyaya:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nyaya:nodejs /app/public ./public

USER nyaya
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
