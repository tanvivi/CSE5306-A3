FROM python:3.11-slim

WORKDIR /app

COPY services/twopc/users_participant/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/twopc/users_participant ./services/twopc/users_participant

ENV PYTHONPATH=/app
ENV PORT=50051
ENV PYTHONUNBUFFERED=1

WORKDIR /app/services/twopc/users_participant
EXPOSE 50051

CMD ["python", "app.py"]
