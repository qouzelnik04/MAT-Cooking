# Appka potřebuje jen standardní knihovnu Pythonu (včetně SQLite) -- žádný pip.
FROM python:3.12-alpine

# Bez tzdata by kontejner běžel v UTC a "dnes" by se o půlnoci posunulo.
RUN apk add --no-cache tzdata

ENV TZ=Europe/Prague \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAT_HOST=0.0.0.0 \
    MAT_PORT=8720 \
    MAT_DATA=/data \
    MAT_ALLOW_ONLY=172.30.32.2

WORKDIR /srv
COPY config.yaml ./
COPY app/ app/
COPY web/ web/

CMD ["python", "-m", "app.main", "--bez-prohlizece"]
