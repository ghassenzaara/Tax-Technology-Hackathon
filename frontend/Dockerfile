# Builds the Klaris frontend and serves it with Caddy (HTTPS, login gate, /api proxy).
# Built by compose.prod.yml in the repository root; the Caddyfile lives next to it.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Same origin as the API: an empty VITE_API_URL makes the adapter call /api/ui directly.
ENV VITE_DATA_SOURCE=http \
    VITE_API_URL=
RUN npm run build

FROM caddy:2.10-alpine
COPY --from=build /app/dist /srv
