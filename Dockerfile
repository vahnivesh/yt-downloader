FROM python:3.10-slim

WORKDIR /app

# Install ffmpeg (REQUIRED for mp3 + merging video/audio)
RUN apt-get update && apt-get install -y ffmpeg && apt-get clean

# Copy project files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Railway provides PORT automatically
ENV PORT=5000

EXPOSE 5000

# Use gunicorn for production
CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]
