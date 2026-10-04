FROM node:22-bookworm-slim
# ffmpeg converts WAV/AIFF to lossless FLAC on upload and import.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
COPY public ./public
COPY docker-entrypoint.sh /usr/local/bin/
ENV NODE_ENV=production STORAGE_DIR=/data PORT=3000
EXPOSE 3000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["node", "src/server.js"]
