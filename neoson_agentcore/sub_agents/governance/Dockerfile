FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ../../neoson_agentcore ./neoson_agentcore
COPY ../../core/glossario_corporativo.py ./core/glossario_corporativo.py
COPY ../../core/__init__.py ./core/__init__.py
COPY main.py .
EXPOSE 8080
CMD ["python", "main.py"]
