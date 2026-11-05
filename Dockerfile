FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    AGENT_REPLAY_PUBLIC=1 \
    AGENT_REPLAY_DATA=/data
RUN mkdir -p /data/traces
EXPOSE 8080
CMD ["sh","-c","agent-replay serve --host ${HOST} --port ${PORT} --public --directory ${AGENT_REPLAY_DATA}/traces"]
