FROM node:20-alpine

WORKDIR /app

# Use npmmirror.com mirror for faster downloads in China
RUN npm config set registry https://registry.npmmirror.com

COPY package.json package-lock.json ./
# BuildKit cache mount preserves npm cache across rebuilds
# npm ci is faster and reproducible compared to npm install
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY . .
EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]