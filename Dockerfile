FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
EXPOSE 9000/udp

ENTRYPOINT ["python", "main.py"]
CMD ["--headless", "--log-level", "INFO"]
