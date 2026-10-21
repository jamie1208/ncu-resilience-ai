# NCU Resilience AI 前端映像：Node 建置靜態檔，nginx 提供服務並把 /api 轉給後端
FROM node:24-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# 建置時只接受公開設定；前端不得放入任何金鑰
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
# 設計系統：minimal（預設）或 playful（ADR-0014）
ARG VITE_DESIGN=minimal
ENV VITE_DESIGN=$VITE_DESIGN
RUN npm run build

FROM nginx:1.29-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
