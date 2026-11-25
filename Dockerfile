FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 MPLBACKEND=Agg
WORKDIR /app
COPY etl/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser . /app
RUN chown appuser:appuser /app
USER appuser
CMD ["python", "etl/etl_pipeline.py"]
