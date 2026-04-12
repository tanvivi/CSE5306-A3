FROM python:3.11-slim

WORKDIR /app

COPY services/twopc/inventory_participant/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/twopc/inventory_participant ./services/twopc/inventory_participant

ENV PYTHONPATH=/app
ENV PORT=50051

WORKDIR /app/services/twopc/inventory_participant
EXPOSE 50051

CMD ["python", "app.py"]
