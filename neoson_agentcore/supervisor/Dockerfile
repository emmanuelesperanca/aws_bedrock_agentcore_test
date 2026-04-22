FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Install OS dependencies (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the neoson_agentcore package (supervisor + runtime)
COPY ../../neoson_agentcore ./neoson_agentcore

# Copy the legacy core/ modules needed by the supervisor
# (glossario_corporativo, security_instructions)
COPY ../../core/glossario_corporativo.py ./core/glossario_corporativo.py
COPY ../../core/__init__.py ./core/__init__.py

# Copy the supervisor entrypoint
COPY main.py .
COPY system_prompt.py .

# AgentCore runtime listens on port 8080
EXPOSE 8080

CMD ["python", "main.py"]
