FROM python:3.14-slim

# Crear directorio de trabajo
WORKDIR /app

# Copiar dependencias y script
COPY requirements.txt .
COPY music_importer_script.py .

# Instalar dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Comando por defecto
CMD ["python", "music_importer_script.py"]
