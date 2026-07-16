FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data/logs data/chroma data/learning

EXPOSE 8080

CMD ["python", "main.py"]
