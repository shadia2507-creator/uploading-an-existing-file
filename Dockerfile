# Imagen para publicar el Ritual Secreto de la Puerta Plateada.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RITUAL_TRUST_PROXY=1

WORKDIR /app
COPY server.py ./
COPY public ./public

# La plataforma define PORT. 8000 es solo el valor por defecto.
EXPOSE 8000
CMD ["python", "server.py", "serve"]
