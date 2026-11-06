FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30" "pydantic>=2.9"
COPY . /app/mocks
EXPOSE 9000
HEALTHCHECK --interval=5s --retries=10 CMD python -c "import urllib.request as u; u.urlopen('http://localhost:9000/healthz')"
CMD ["uvicorn", "mocks.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "9000"]
