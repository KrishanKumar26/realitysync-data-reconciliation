# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# RealitySync web
#
#   development  — hot-reloading dev server, used by docker compose
#   runner       — standalone production server
# ---------------------------------------------------------------------------

FROM node:20-alpine AS deps
WORKDIR /srv/web
COPY package.json package-lock.json* ./
RUN npm ci

# --- Development ------------------------------------------------------------
FROM node:20-alpine AS development
WORKDIR /srv/web
ENV NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /srv/web/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

# --- Production build -------------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /srv/web
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /srv/web/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are inlined at build time, so they must be present here.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_ENVIRONMENT=production
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_ENVIRONMENT=$NEXT_PUBLIC_ENVIRONMENT
RUN npm run build

# --- Production runtime -----------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /srv/web
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
RUN addgroup -g 10001 -S nodejs && adduser -S -u 10001 -G nodejs nextjs
COPY --from=builder /srv/web/public ./public
COPY --from=builder --chown=nextjs:nodejs /srv/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /srv/web/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
