# Stage 1: build the UI.
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: the API and worker share one image and differ only in the command.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
# Sample documents and the evaluation set, so tests and the eval run inside the image.
COPY samples ./samples
COPY eval ./eval
COPY --from=ui /ui/dist ./static
RUN useradd --create-home app && chown -R app /srv
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
