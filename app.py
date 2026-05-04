from flask import Flask, request, jsonify, send_file, render_template
import yt_dlp
import os
import uuid
import threading

# Flask setup
app = Flask(__name__, template_folder="templates")

# Folder for downloads
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Store job states
jobs = {}

# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download():
    data = request.json
    url = data.get("url")
    fmt = data.get("format")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "progress": 0,
        "status": "Starting...",
        "file": None
    }

    # Run download in background
    thread = threading.Thread(target=process_download, args=(job_id, url, fmt))
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/progress/<job_id>")
def progress(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"progress": 0, "status": "Invalid job"})
    return jsonify({
        "progress": job["progress"],
        "status": job["status"]
    })


@app.route("/file/<job_id>")
def get_file(job_id):
    job = jobs.get(job_id)

    if not job or not job["file"]:
        return "File not ready", 404

    return send_file(job["file"], as_attachment=True)


# =========================
# DOWNLOAD LOGIC
# =========================

def process_download(job_id, url, fmt):
    def progress_hook(d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '0%').replace('%', '').strip()
            try:
                jobs[job_id]["progress"] = float(percent)
                jobs[job_id]["status"] = "Downloading..."
            except:
                pass

        elif d['status'] == 'finished':
            jobs[job_id]["progress"] = 95
            jobs[job_id]["status"] = "Processing..."

    file_id = str(uuid.uuid4())

    try:
        if fmt == "mp3":
            output_template = f"{DOWNLOAD_FOLDER}/{file_id}.%(ext)s"
            final_file = f"{DOWNLOAD_FOLDER}/{file_id}.mp3"

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_template,
                'progress_hooks': [progress_hook],
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                    'cookiefile': 'cookies.txt',
                }]
            }

        else:
            final_file = f"{DOWNLOAD_FOLDER}/{file_id}.mp4"

            ydl_opts = {
                'format': 'bestvideo+bestaudio/best',
                'outtmpl': final_file,
                'merge_output_format': 'mp4',
                'progress_hooks': [progress_hook],
                'cookiefile': 'cookies.txt',
            }

        # Run download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Done
        jobs[job_id]["progress"] = 100
        jobs[job_id]["status"] = "Done"
        jobs[job_id]["file"] = final_file

    except Exception as e:
        jobs[job_id]["status"] = f"Error: {str(e)}"
        jobs[job_id]["progress"] = 0


# =========================
# RUN (Railway compatible)
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Railway dynamic port
    app.run(host="0.0.0.0", port=port)
