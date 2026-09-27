FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
USER 65532:65532
EXPOSE 8080
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8080", "--timeout", "180", "app:app"]
