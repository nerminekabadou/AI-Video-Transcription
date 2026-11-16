# Live demo image: static Next.js build + FastAPI backend on a single origin.
# Listens on $PORT (set by Render; default 7860).
FROM node:20-slim AS frontend
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NEXT_OUTPUT=export NEXT_PUBLIC_API_URL="" NEXT_PUBLIC_SAMPLE_VIDEO=/samples/MachineLearning.mp4
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user main.py .
COPY --chown=user samples ./samples
COPY --from=frontend --chown=user /src/out ./static

USER user
ENV STATIC_DIR=/home/user/app/static \
    SAMPLES_DIR=/home/user/app/samples \
    MAX_UPLOAD_MB=25
EXPOSE 7860
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
