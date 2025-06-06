FROM python:3.11.8-slim-bullseye

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get upgrade -y && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN pip install --force-reinstall --no-cache-dir aiogram==2.25.2

CMD ["python", "main.py"]
