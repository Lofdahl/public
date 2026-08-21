FROM python:3.11-slim
# Installera Flask
RUN pip install flask
# Installera requests
RUN pip install requests
# Skapa app-katalog
WORKDIR /app
# Kopiera in applikationen
COPY app.py /app/app.py
# Skapa data-katalog i containern
RUN mkdir -p /app/data
# Exponera porten Flask kör på
EXPOSE 8080
# Startkommandot
CMD ["python", "app.py"]
