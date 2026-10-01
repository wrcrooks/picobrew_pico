FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8090 \
    SIM_DATA_DIR=/data \
    PICOBREW_SERVER_URL=http://host.docker.internal:8080

WORKDIR /simulator

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY picosim ./picosim
COPY run.py .

VOLUME /data
EXPOSE 8090

CMD ["python", "run.py"]
