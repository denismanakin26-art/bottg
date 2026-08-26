# Dockerfile для Telegram бота
FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей для matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости и устанавливаем
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY bot.py .

# Создаём volume для базы данных (чтобы данные сохранялись при перезапуске)
VOLUME ["/app/data"]

# Запуск бота
CMD ["python", "bot.py"]
