FROM python:3.11-slim

WORKDIR /app

COPY services/twopc/coordinator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/twopc/coordinator ./services/twopc/coordinator

ENV PYTHONPATH=/app
ENV PORT=50051
ENV PYTHONUNBUFFERED=1

WORKDIR /app/services/twopc/coordinator
EXPOSE 50051

CMD ["python", "app.py"]
