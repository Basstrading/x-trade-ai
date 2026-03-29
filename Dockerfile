FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data/risk_desk logs

EXPOSE 8001

# Defaults — override via .env ou docker-compose
ENV TRADING_PORT=8001
ENV PROP_FIRM=topstep
ENV TOPSTEP_ACCOUNT_TYPE=50k
ENV INSTRUMENT=MNQ
ENV TRADER_ID=trader_1

CMD ["python", "main.py"]
