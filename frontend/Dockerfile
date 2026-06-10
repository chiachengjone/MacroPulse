# syntax=docker/dockerfile:1

# ---- deps: install node_modules from a clean lockfile ----
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder: compile the Next.js standalone bundle ----
FROM node:22-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are inlined at build time. This is the public backend URL.
ENV NEXT_PUBLIC_API_URL=https://macropulse-270431042772.us-central1.run.app
# Emit .next/standalone bundle needed for the minimal runtime stage below.
ENV BUILD_STANDALONE=true
RUN npm run build

# ---- runner: minimal runtime image ----
FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=8080
ENV HOSTNAME=0.0.0.0
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 8080
CMD ["node", "server.js"]
