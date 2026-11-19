# One image for every Python service of the lab: the gateway, triage, the
# drafter, the nightly report, the LLM stand-in and the simulated world. Each
# container runs a different command from compose.yaml.
FROM python:3.12-slim

WORKDIR /app

# Dependencies first: this layer is rebuilt only when requirements.txt
# changes, not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The complaints and the code.
COPY data/ data/
COPY lab/ lab/

# Print logs at once instead of buffering them, so `docker compose logs`
# shows what is happening now.
ENV PYTHONUNBUFFERED=1
