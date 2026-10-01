FROM node:24.21.0-alpine3.24@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
# This public, relative URL works unchanged behind HTTP or HTTPS in any environment.
ENV VITE_API_BASE_URL=/api/v1
COPY tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.5-alpine3.24@sha256:ed04ec1ff34502c339ee5c3ae3f855442398edc1d05591e2b98981dcbbd20b1e
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/locations.conf nginx/security-headers.conf /etc/nginx/orderflow/
COPY --from=build /app/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
# Skip image entrypoint scripts: configuration is immutable and rootfs may be read-only.
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
